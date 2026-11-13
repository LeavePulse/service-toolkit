//! Downloads that land only when they are what was expected.
//!
//! [`verified`] streams a URL into a sibling of the target, hashes it on the
//! way, and moves it into place with [`crate::fs::persist`] only on a match. A
//! mismatch, a dropped connection or an error status leaves the target exactly
//! as it was (absent, or the previous version) and removes the sibling: a
//! half-downloaded or tampered file is never at the final path, where the next
//! pass's existence check would take it for a good one.
//!
//! The body is never held whole in memory (a JDK is ~200 MB). Requests go
//! through the toolkit's [`crate::http::Client`], so they carry its retries
//! (connection failures, timeouts and `429`/`503` on a GET) and errors that
//! name the URL. The default client has a connect timeout and an idle read
//! timeout but no whole-request timeout: a large file on a slow but alive
//! connection legitimately takes minutes, while a stalled one is cut after
//! [`READ_TIMEOUT`] of silence. A failure mid-body is not resumed; the next
//! call starts over.

use std::fmt;
use std::io;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;
use std::time::Duration;

use tokio::io::AsyncWriteExt;

use crate::digest::{Algorithm, Digest, Hasher};
use crate::fs::{self as atomic, Access};
use crate::http::{Client, HttpError, HttpErrorKind, HttpPolicy};

/// Connect timeout of the default client.
pub const CONNECT_TIMEOUT: Duration = Duration::from_secs(15);
/// Longest silence between body chunks the default client accepts.
pub const READ_TIMEOUT: Duration = Duration::from_secs(45);

/// Progress callback: bytes written so far and the total when the server sent
/// a length. Called when the whole percentage changes (or every MiB without a
/// length) and once at the end, not per chunk: consumers forward each report
/// over IPC to every window.
pub type Progress<'a> = &'a (dyn Fn(u64, Option<u64>) + Send + Sync);

/// How a download runs. `Options::default()` is the shared client, no
/// progress, [`Access::Shared`], and always downloading.
#[derive(Clone, Copy, Default)]
pub struct Options<'a> {
    /// The client to use; `None` takes the module's shared default.
    pub client: Option<&'a Client>,
    pub progress: Option<Progress<'a>>,
    /// Mode of the landed file (`Executable` for a binary that is run next).
    pub access: Access,
    /// Keep a target that is already there: for [`verified_with`] only when
    /// it matches the digest, for [`unverified_with`] whenever it exists.
    pub reuse_existing: bool,
}

/// What a successful call did.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Outcome {
    /// Downloaded and moved into place; `hex` is the digest of what landed.
    Downloaded { hex: String },
    /// The target was already there and was kept (`reuse_existing`).
    Reused,
}

#[derive(Debug)]
pub enum Error {
    /// The request failed, the server answered with an error status, or the
    /// body broke off. Carries the URL.
    Http(HttpError),
    /// The body arrived whole and is not what was expected.
    Mismatch {
        url: String,
        expected: Digest,
        actual: String,
    },
    /// The local side failed: creating, writing or moving the file.
    Io { path: PathBuf, source: io::Error },
}

impl Error {
    /// The status of an error answer, if that is what this is.
    pub fn status(&self) -> Option<reqwest::StatusCode> {
        match self {
            Error::Http(error) => error.status(),
            Error::Mismatch { .. } | Error::Io { .. } => None,
        }
    }
}

impl From<HttpError> for Error {
    fn from(error: HttpError) -> Self {
        Error::Http(error)
    }
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Error::Http(error) => write!(f, "{error}"),
            Error::Mismatch {
                url,
                expected,
                actual,
            } => write!(
                f,
                "{} mismatch for {url}: expected {}, got {actual}",
                expected.algorithm(),
                expected.hex()
            ),
            Error::Io { path, source } => write!(f, "{}: {source}", path.display()),
        }
    }
}

impl std::error::Error for Error {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Error::Http(error) => Some(error),
            Error::Io { source, .. } => Some(source),
            Error::Mismatch { .. } => None,
        }
    }
}

static CLIENT: OnceLock<Client> = OnceLock::new();

/// The policy of the shared default client: no whole-request timeout, an idle
/// read timeout, the toolkit's default retries. A consumer with its own user
/// agent or proxy settings starts from this and passes its client in
/// [`Options::client`].
pub fn default_policy() -> HttpPolicy {
    let mut policy = HttpPolicy::new(concat!("service-toolkit-rust/", env!("CARGO_PKG_VERSION")));
    policy.connect_timeout = CONNECT_TIMEOUT;
    policy.timeout = None;
    policy.read_timeout = Some(READ_TIMEOUT);
    policy
}

fn default_client() -> Result<&'static Client, HttpError> {
    if let Some(client) = CLIENT.get() {
        return Ok(client);
    }
    let client = default_policy().build()?;
    Ok(CLIENT.get_or_init(|| client))
}

/// Download *url* to *path* and keep it only if it has digest *expected*.
pub async fn verified(url: &str, path: &Path, expected: &Digest) -> Result<(), Error> {
    verified_with(url, path, expected, &Options::default())
        .await
        .map(|_| ())
}

/// [`verified`] with [`Options`].
pub async fn verified_with(
    url: &str,
    path: &Path,
    expected: &Digest,
    options: &Options<'_>,
) -> Result<Outcome, Error> {
    if options.reuse_existing && already_matches(path, expected).await {
        return Ok(Outcome::Reused);
    }
    fetch(url, path, expected.algorithm(), Some(expected), options).await
}

/// Download *url* to *path* with nothing to check it against, still atomically,
/// and return the *algorithm* digest of what landed (for a caller that checks
/// it against a manifest afterwards, or records it).
pub async fn unverified_with(
    url: &str,
    path: &Path,
    algorithm: Algorithm,
    options: &Options<'_>,
) -> Result<Outcome, Error> {
    if options.reuse_existing && tokio::fs::try_exists(path).await.unwrap_or(false) {
        return Ok(Outcome::Reused);
    }
    fetch(url, path, algorithm, None, options).await
}

/// Whether *path* exists and has digest *expected*. An unreadable file counts
/// as not matching, so it is downloaded again.
async fn already_matches(path: &Path, expected: &Digest) -> bool {
    let path = path.to_path_buf();
    let expected = expected.clone();
    tokio::task::spawn_blocking(move || crate::digest::file_matches(&path, &expected))
        .await
        .ok()
        .and_then(Result::ok)
        .unwrap_or(false)
}

async fn blocking<T, F>(path: &Path, work: F) -> Result<T, Error>
where
    T: Send + 'static,
    F: FnOnce() -> io::Result<T> + Send + 'static,
{
    tokio::task::spawn_blocking(work)
        .await
        .map_err(io::Error::other)
        .and_then(|result| result)
        .map_err(|source| Error::Io {
            path: path.to_path_buf(),
            source,
        })
}

async fn fetch(
    url: &str,
    path: &Path,
    algorithm: Algorithm,
    expected: Option<&Digest>,
    options: &Options<'_>,
) -> Result<Outcome, Error> {
    let client = match options.client {
        Some(client) => client,
        None => default_client()?,
    };
    let mut response = client.send(client.get(url)).await?;
    // `send` passes a 304 through; with no validator sent it carries no body.
    let status = response.status();
    if !status.is_success() {
        return Err(Error::Http(HttpError {
            url: url.to_string(),
            kind: HttpErrorKind::Status {
                status,
                body: String::new(),
            },
        }));
    }
    let body = |source| {
        Error::Http(HttpError {
            url: url.to_string(),
            kind: HttpErrorKind::Transport(source),
        })
    };

    if let Some(parent) = path.parent().filter(|p| !p.as_os_str().is_empty()) {
        tokio::fs::create_dir_all(parent)
            .await
            .map_err(|source| Error::Io {
                path: parent.to_path_buf(),
                source,
            })?;
    }
    let target = path.to_path_buf();
    let access = options.access;
    let (file, temp) = blocking(path, move || atomic::create_temp(&target, access)).await?;
    let mut file = tokio::fs::File::from_std(file);

    let total = response.content_length();
    let mut hasher = Hasher::new(algorithm);
    let mut meter = Meter::new(options.progress, total);
    let streamed: Result<(), Error> = async {
        let io = |source| Error::Io {
            path: temp.clone(),
            source,
        };
        while let Some(chunk) = response.chunk().await.map_err(body)? {
            hasher.update(&chunk);
            file.write_all(&chunk).await.map_err(io)?;
            meter.advance(chunk.len() as u64);
        }
        file.flush().await.map_err(io)?;
        file.sync_all().await.map_err(io)
    }
    .await;
    drop(file);
    if let Err(error) = streamed {
        let _ = tokio::fs::remove_file(&temp).await;
        return Err(error);
    }
    meter.finish();

    let actual = hasher.finalize_hex();
    if let Some(expected) = expected
        && !expected.matches(&actual)
    {
        let _ = tokio::fs::remove_file(&temp).await;
        return Err(Error::Mismatch {
            url: url.to_string(),
            expected: expected.clone(),
            actual,
        });
    }

    let target = path.to_path_buf();
    blocking(path, move || atomic::persist(&temp, &target)).await?;
    Ok(Outcome::Downloaded { hex: actual })
}

/// Throttles progress reports to one per percent (or per MiB without a
/// length), plus a final one.
struct Meter<'a> {
    progress: Option<Progress<'a>>,
    total: Option<u64>,
    done: u64,
    last_mark: Option<u64>,
    reported: Option<u64>,
}

impl<'a> Meter<'a> {
    fn new(progress: Option<Progress<'a>>, total: Option<u64>) -> Self {
        Self {
            progress,
            total,
            done: 0,
            last_mark: None,
            reported: None,
        }
    }

    fn advance(&mut self, bytes: u64) {
        self.done += bytes;
        let Some(progress) = self.progress else {
            return;
        };
        let mark = match self.total.filter(|total| *total > 0) {
            Some(total) => self.done.saturating_mul(100) / total,
            None => self.done / 1_048_576,
        };
        if self.last_mark != Some(mark) {
            self.last_mark = Some(mark);
            self.reported = Some(self.done);
            progress(self.done, self.total);
        }
    }

    fn finish(&mut self) {
        if let Some(progress) = self.progress
            && self.reported != Some(self.done)
        {
            self.reported = Some(self.done);
            progress(self.done, self.total);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::digest::sha256_hex;
    use std::io::{Read, Write};
    use std::net::TcpListener;
    use std::sync::Arc;
    use std::sync::Mutex;
    use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};

    /// A one-thread HTTP/1.1 server on loopback: `/ok` serves *body*, any other
    /// path is a 404. Counts the requests it answered.
    struct Server {
        base: String,
        hits: Arc<AtomicUsize>,
    }

    fn serve(body: Vec<u8>) -> Server {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let base = format!("http://{}", listener.local_addr().unwrap());
        let hits = Arc::new(AtomicUsize::new(0));
        let counted = Arc::clone(&hits);
        std::thread::spawn(move || {
            for stream in listener.incoming() {
                let Ok(mut stream) = stream else { break };
                let mut request = Vec::new();
                let mut buf = [0u8; 1024];
                while !request.windows(4).any(|w| w == b"\r\n\r\n") {
                    match stream.read(&mut buf) {
                        Ok(0) | Err(_) => break,
                        Ok(n) => request.extend_from_slice(&buf[..n]),
                    }
                }
                counted.fetch_add(1, Ordering::SeqCst);
                let line = String::from_utf8_lossy(&request);
                let (status, payload): (&str, &[u8]) = if line.starts_with("GET /ok ") {
                    ("200 OK", &body)
                } else {
                    ("404 Not Found", b"missing")
                };
                let head = format!(
                    "HTTP/1.1 {status}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                    payload.len()
                );
                let _ = stream.write_all(head.as_bytes());
                let _ = stream.write_all(payload);
            }
        });
        Server { base, hits }
    }

    fn client() -> Client {
        let mut policy = default_policy();
        policy.retries = crate::http::Retries::NONE;
        policy.build().unwrap()
    }

    fn scratch(name: &str) -> PathBuf {
        static N: AtomicU64 = AtomicU64::new(0);
        let dir = std::env::temp_dir().join(format!(
            "service-toolkit-download-{}-{}-{name}",
            std::process::id(),
            N.fetch_add(1, Ordering::Relaxed)
        ));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn entries(dir: &Path) -> Vec<String> {
        let mut names: Vec<String> = std::fs::read_dir(dir)
            .unwrap()
            .map(|entry| entry.unwrap().file_name().to_string_lossy().into_owned())
            .collect();
        names.sort();
        names
    }

    #[tokio::test]
    async fn a_matching_body_lands_and_creates_the_directory() {
        let body = b"jar bytes".to_vec();
        let server = serve(body.clone());
        let dir = scratch("ok");
        let path = dir.join("mods").join("a.jar");
        let client = client();
        let options = Options {
            client: Some(&client),
            ..Options::default()
        };
        let outcome = verified_with(
            &format!("{}/ok", server.base),
            &path,
            &Digest::sha256(sha256_hex(&body)),
            &options,
        )
        .await
        .unwrap();
        assert_eq!(
            outcome,
            Outcome::Downloaded {
                hex: sha256_hex(&body)
            }
        );
        assert_eq!(std::fs::read(&path).unwrap(), body);
        assert_eq!(entries(&dir.join("mods")), ["a.jar"]);
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[tokio::test]
    async fn a_mismatch_keeps_the_previous_file_and_leaves_no_sibling() {
        let server = serve(b"tampered".to_vec());
        let dir = scratch("mismatch");
        let path = dir.join("agent");
        std::fs::write(&path, b"previous").unwrap();
        let client = client();
        let options = Options {
            client: Some(&client),
            ..Options::default()
        };
        let error = verified_with(
            &format!("{}/ok", server.base),
            &path,
            &Digest::sha256(sha256_hex(b"genuine")),
            &options,
        )
        .await
        .unwrap_err();
        assert!(matches!(error, Error::Mismatch { .. }), "{error:?}");
        assert!(error.to_string().starts_with("sha256 mismatch for http://"));
        assert_eq!(std::fs::read(&path).unwrap(), b"previous");
        assert_eq!(entries(&dir), ["agent"]);
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[tokio::test]
    async fn an_error_status_writes_nothing() {
        let server = serve(Vec::new());
        let dir = scratch("status");
        let path = dir.join("file");
        let client = client();
        let options = Options {
            client: Some(&client),
            ..Options::default()
        };
        let url = format!("{}/gone", server.base);
        let error = verified_with(&url, &path, &Digest::sha1("00"), &options)
            .await
            .unwrap_err();
        assert_eq!(error.status().map(|s| s.as_u16()), Some(404), "{error:?}");
        assert!(error.to_string().contains(&url), "{error}");
        assert!(entries(&dir).is_empty());
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[tokio::test]
    async fn a_matching_existing_file_is_reused_without_a_request() {
        let body = b"cached".to_vec();
        let server = serve(body.clone());
        let dir = scratch("reuse");
        let path = dir.join("asset");
        std::fs::write(&path, &body).unwrap();
        let client = client();
        let options = Options {
            client: Some(&client),
            reuse_existing: true,
            ..Options::default()
        };
        let url = format!("{}/ok", server.base);
        let outcome = verified_with(
            &url,
            &path,
            &Digest::sha1(crate::digest::sha1_hex(&body)),
            &options,
        )
        .await
        .unwrap();
        assert_eq!(outcome, Outcome::Reused);
        assert_eq!(server.hits.load(Ordering::SeqCst), 0);

        // A stale file is replaced.
        std::fs::write(&path, b"stale").unwrap();
        let outcome = verified_with(
            &url,
            &path,
            &Digest::sha1(crate::digest::sha1_hex(&body)),
            &options,
        )
        .await
        .unwrap();
        assert!(matches!(outcome, Outcome::Downloaded { .. }));
        assert_eq!(std::fs::read(&path).unwrap(), body);
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[tokio::test]
    async fn progress_is_throttled_and_ends_at_the_total() {
        let body = vec![1u8; 3 * 1024 * 1024];
        let server = serve(body.clone());
        let dir = scratch("progress");
        let path = dir.join("jdk.tar.gz");
        let reports = Mutex::new(Vec::new());
        let progress = |done: u64, total: Option<u64>| reports.lock().unwrap().push((done, total));
        let client = client();
        let options = Options {
            client: Some(&client),
            progress: Some(&progress),
            ..Options::default()
        };
        let outcome = unverified_with(
            &format!("{}/ok", server.base),
            &path,
            Algorithm::Sha512,
            &options,
        )
        .await
        .unwrap();
        assert_eq!(
            outcome,
            Outcome::Downloaded {
                hex: crate::digest::sha512_hex(&body)
            }
        );
        let reports = reports.into_inner().unwrap();
        let total = body.len() as u64;
        assert!(reports.len() <= 102, "{} reports", reports.len());
        assert_eq!(reports.last(), Some(&(total, Some(total))));
        assert!(reports.windows(2).all(|w| w[0].0 < w[1].0));
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn an_executable_download_lands_0755() {
        use std::os::unix::fs::PermissionsExt;
        let body = b"#!/bin/sh\n".to_vec();
        let server = serve(body.clone());
        let dir = scratch("exec");
        let path = dir.join("tool");
        let client = client();
        let options = Options {
            client: Some(&client),
            access: Access::Executable,
            ..Options::default()
        };
        verified_with(
            &format!("{}/ok", server.base),
            &path,
            &Digest::sha256(sha256_hex(&body)),
            &options,
        )
        .await
        .unwrap();
        let mode = std::fs::metadata(&path).unwrap().permissions().mode() & 0o7777;
        assert_eq!(mode, 0o755);
        std::fs::remove_dir_all(&dir).unwrap();
    }
}
