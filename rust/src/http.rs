//! HTTP client side of the toolkit's Transfer layer.
//!
//! A service declares how it talks to a peer as data ([`HttpPolicy`]) and gets a
//! [`Client`] that applies it: user agent, timeouts, redirect policy, content
//! decoding, bounded retries that honour `Retry-After`, errors that name the
//! URL, and conditional GET over a pluggable [`EtagStore`].
//!
//! Decoding of `zstd`, `br` and `gzip` is reqwest's (features enabled here), so
//! it matches the Python `Transfer` server without code of ours. A peer on the
//! same host or inside the cluster should be reached with
//! [`Encoding::Identity`]: compression on loopback only spends CPU on both ends.

use std::collections::{HashMap, VecDeque};
use std::fmt;
use std::sync::Mutex;
use std::time::{Duration, SystemTime};

use bytes::Bytes;
use reqwest::header::{
    ACCEPT_ENCODING, AUTHORIZATION, CACHE_CONTROL, ETAG, HeaderMap, HeaderValue, IF_NONE_MATCH,
    RETRY_AFTER,
};
use reqwest::{Method, RequestBuilder, Response, StatusCode, redirect};

/// How the client asks for compressed bodies.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Encoding {
    /// `zstd, br, gzip`, decoded transparently. For peers across a network.
    Negotiate,
    /// `identity`. For peers on the same host or inside the cluster.
    Identity,
}

/// Retry policy. Connection failures are retried for every method (the
/// request never reached the peer); timeouts and dropped responses only for
/// idempotent methods; `429` and `503` for idempotent methods, waiting what
/// `Retry-After` asks within `max_wait`.
#[derive(Clone, Debug)]
pub struct Retries {
    pub attempts: u32,
    pub base: Duration,
    pub max_wait: Duration,
}

impl Default for Retries {
    fn default() -> Self {
        Self {
            attempts: 3,
            base: Duration::from_millis(250),
            max_wait: Duration::from_secs(30),
        }
    }
}

impl Retries {
    pub const NONE: Self = Self {
        attempts: 0,
        base: Duration::ZERO,
        max_wait: Duration::ZERO,
    };

    fn backoff(&self, attempt: u32) -> Duration {
        let factor = 1u32.checked_shl(attempt).unwrap_or(u32::MAX);
        self.base.saturating_mul(factor).min(self.max_wait)
    }
}

/// Declared transport policy for one peer.
#[derive(Clone, Debug)]
pub struct HttpPolicy {
    pub user_agent: String,
    pub connect_timeout: Duration,
    /// Whole-request timeout; `None` for streams and large downloads.
    pub timeout: Option<Duration>,
    /// Idle read timeout between body chunks.
    pub read_timeout: Option<Duration>,
    pub follow_redirects: bool,
    pub encoding: Encoding,
    pub retries: Retries,
    pub bearer: Option<String>,
}

impl HttpPolicy {
    pub fn new(user_agent: impl Into<String>) -> Self {
        Self {
            user_agent: user_agent.into(),
            connect_timeout: Duration::from_secs(5),
            timeout: Some(Duration::from_secs(30)),
            read_timeout: None,
            follow_redirects: true,
            encoding: Encoding::Negotiate,
            retries: Retries::default(),
            bearer: None,
        }
    }

    pub fn build(&self) -> Result<Client, HttpError> {
        let mut headers = HeaderMap::new();
        if self.encoding == Encoding::Identity {
            headers.insert(ACCEPT_ENCODING, HeaderValue::from_static("identity"));
        }
        if let Some(token) = &self.bearer {
            let mut value = HeaderValue::from_str(&format!("Bearer {token}"))
                .map_err(|_| HttpError::config("bearer token is not a valid header value"))?;
            value.set_sensitive(true);
            headers.insert(AUTHORIZATION, value);
        }
        let negotiate = self.encoding == Encoding::Negotiate;
        let mut builder = reqwest::Client::builder()
            .user_agent(&self.user_agent)
            .connect_timeout(self.connect_timeout)
            .default_headers(headers)
            .zstd(negotiate)
            .brotli(negotiate)
            .gzip(negotiate)
            .redirect(if self.follow_redirects {
                redirect::Policy::limited(10)
            } else {
                redirect::Policy::none()
            });
        if let Some(timeout) = self.timeout {
            builder = builder.timeout(timeout);
        }
        if let Some(read) = self.read_timeout {
            builder = builder.read_timeout(read);
        }
        let inner = builder
            .build()
            .map_err(|e| HttpError::config(&e.to_string()))?;
        Ok(Client {
            inner,
            retries: self.retries.clone(),
        })
    }
}

/// What went wrong, always with the URL it went wrong on.
#[derive(Debug)]
pub struct HttpError {
    pub url: String,
    pub kind: HttpErrorKind,
}

#[derive(Debug)]
pub enum HttpErrorKind {
    Config(String),
    Transport(reqwest::Error),
    Status {
        status: StatusCode,
        body: String,
    },
    /// A `304` arrived for a request that carried no validator and asked every
    /// cache to stand aside: nothing can produce the body.
    StaleValidator,
}

impl HttpError {
    fn config(message: &str) -> Self {
        Self {
            url: String::new(),
            kind: HttpErrorKind::Config(message.to_owned()),
        }
    }

    pub fn status(&self) -> Option<StatusCode> {
        match &self.kind {
            HttpErrorKind::Status { status, .. } => Some(*status),
            HttpErrorKind::Transport(e) => e.status(),
            _ => None,
        }
    }
}

impl fmt::Display for HttpError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match &self.kind {
            HttpErrorKind::Config(m) => write!(f, "http client config: {m}"),
            HttpErrorKind::Transport(e) => write!(f, "{}: {e}", self.url),
            HttpErrorKind::Status { status, body } => {
                write!(f, "{}: {status}", self.url)?;
                if !body.is_empty() {
                    write!(f, ": {}", body.chars().take(200).collect::<String>())?;
                }
                Ok(())
            }
            HttpErrorKind::StaleValidator => {
                write!(f, "{}: 304 to an unconditional request", self.url)
            }
        }
    }
}

impl std::error::Error for HttpError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match &self.kind {
            HttpErrorKind::Transport(e) => Some(e),
            _ => None,
        }
    }
}

/// A client built from an [`HttpPolicy`]. Cheap to clone; clones share the
/// connection pool.
#[derive(Clone, Debug)]
pub struct Client {
    inner: reqwest::Client,
    retries: Retries,
}

fn idempotent(method: &Method) -> bool {
    matches!(
        *method,
        Method::GET | Method::HEAD | Method::OPTIONS | Method::PUT | Method::DELETE
    )
}

fn retry_after(response: &Response) -> Option<Duration> {
    let value = response.headers().get(RETRY_AFTER)?.to_str().ok()?.trim();
    if let Ok(seconds) = value.parse::<u64>() {
        return Some(Duration::from_secs(seconds));
    }
    let at = httpdate::parse_http_date(value).ok()?;
    Some(
        at.duration_since(SystemTime::now())
            .unwrap_or(Duration::ZERO),
    )
}

impl Client {
    pub fn inner(&self) -> &reqwest::Client {
        &self.inner
    }

    pub fn request(&self, method: Method, url: &str) -> RequestBuilder {
        self.inner.request(method, url)
    }

    pub fn get(&self, url: &str) -> RequestBuilder {
        self.inner.get(url)
    }

    /// Send with the policy's retries and turn a non-2xx answer into an
    /// [`HttpError`]. `304` passes through: it is an answer, not a failure.
    /// A request whose body cannot be cloned (a stream) is sent once.
    pub async fn send(&self, request: RequestBuilder) -> Result<Response, HttpError> {
        let request = request.build().map_err(|e| HttpError {
            url: e.url().map(|u| u.to_string()).unwrap_or_default(),
            kind: HttpErrorKind::Transport(e),
        })?;
        let url = request.url().to_string();
        let replayable = idempotent(request.method());
        let mut attempt = 0;
        let mut pending = Some(request);
        loop {
            let Some(current) = pending.take() else {
                unreachable!("a request is always pending at the top of the loop")
            };
            let spare = if attempt < self.retries.attempts {
                current.try_clone()
            } else {
                None
            };
            let wait = match self.inner.execute(current).await {
                Ok(response) => {
                    let status = response.status();
                    if status.is_success() || status == StatusCode::NOT_MODIFIED {
                        return Ok(response);
                    }
                    let throttled = matches!(
                        status,
                        StatusCode::TOO_MANY_REQUESTS | StatusCode::SERVICE_UNAVAILABLE
                    );
                    let wait =
                        retry_after(&response).unwrap_or_else(|| self.retries.backoff(attempt));
                    if spare.is_none() || !throttled || !replayable || wait > self.retries.max_wait
                    {
                        let body = response.text().await.unwrap_or_default();
                        return Err(HttpError {
                            url,
                            kind: HttpErrorKind::Status { status, body },
                        });
                    }
                    wait
                }
                Err(error) => {
                    let unsent = error.is_connect();
                    let lost = error.is_timeout() || error.is_request() || error.is_body();
                    if spare.is_none() || !(unsent || (replayable && lost)) {
                        return Err(HttpError {
                            url,
                            kind: HttpErrorKind::Transport(error),
                        });
                    }
                    self.retries.backoff(attempt)
                }
            };
            tokio::time::sleep(wait).await;
            pending = spare;
            attempt += 1;
        }
    }

    /// Fetch through an ETag cache: send the stored validator, reuse the stored
    /// body on `304`, store the new one on `200`. `Ok(None)` on `404`, with the
    /// stale entry dropped. A `304` for a body the store no longer holds is
    /// recovered with one unconditional request that asks caches to stand
    /// aside.
    pub async fn conditional_get(
        &self,
        url: &str,
        store: &dyn EtagStore,
    ) -> Result<Option<Bytes>, HttpError> {
        let cached = store.get(url);
        let mut request = self.get(url);
        if let Some(entry) = &cached {
            request = request.header(IF_NONE_MATCH, entry.etag.as_str());
        }
        match self.fetch(url, request, store).await? {
            Fetched::Body(body) => Ok(Some(body)),
            Fetched::Missing => Ok(None),
            Fetched::NotModified => {
                if let Some(entry) = cached {
                    return Ok(Some(entry.body));
                }
                let bypass = self.get(url).header(CACHE_CONTROL, "no-cache");
                match self.fetch(url, bypass, store).await? {
                    Fetched::Body(body) => Ok(Some(body)),
                    Fetched::Missing => Ok(None),
                    Fetched::NotModified => {
                        store.remove(url);
                        Err(HttpError {
                            url: url.to_owned(),
                            kind: HttpErrorKind::StaleValidator,
                        })
                    }
                }
            }
        }
    }

    async fn fetch(
        &self,
        url: &str,
        request: RequestBuilder,
        store: &dyn EtagStore,
    ) -> Result<Fetched, HttpError> {
        let response = match self.send(request).await {
            Ok(response) => response,
            Err(error) if error.status() == Some(StatusCode::NOT_FOUND) => {
                store.remove(url);
                return Ok(Fetched::Missing);
            }
            Err(error) => return Err(error),
        };
        if response.status() == StatusCode::NOT_MODIFIED {
            return Ok(Fetched::NotModified);
        }
        let etag = response
            .headers()
            .get(ETAG)
            .and_then(|v| v.to_str().ok())
            .map(str::to_owned);
        let body = response.bytes().await.map_err(|e| HttpError {
            url: url.to_owned(),
            kind: HttpErrorKind::Transport(e),
        })?;
        match etag {
            Some(etag) => store.put(
                url,
                EtagEntry {
                    etag,
                    body: body.clone(),
                },
            ),
            None => store.remove(url),
        }
        Ok(Fetched::Body(body))
    }
}

enum Fetched {
    Body(Bytes),
    NotModified,
    Missing,
}

/// A stored validator and the body it belongs to.
#[derive(Clone, Debug)]
pub struct EtagEntry {
    pub etag: String,
    pub body: Bytes,
}

/// Where conditional GET keeps validators. Implement it over a file or a
/// database to keep them across restarts.
pub trait EtagStore: Send + Sync {
    fn get(&self, key: &str) -> Option<EtagEntry>;
    fn put(&self, key: &str, entry: EtagEntry);
    fn remove(&self, key: &str);
}

/// In-process store, least recently used entries evicted past `limit`.
#[derive(Debug)]
pub struct MemoryEtagStore {
    limit: usize,
    state: Mutex<(HashMap<String, EtagEntry>, VecDeque<String>)>,
}

impl MemoryEtagStore {
    pub fn new(limit: usize) -> Self {
        Self {
            limit: limit.max(1),
            state: Mutex::new((HashMap::new(), VecDeque::new())),
        }
    }
}

impl Default for MemoryEtagStore {
    fn default() -> Self {
        Self::new(500)
    }
}

impl EtagStore for MemoryEtagStore {
    fn get(&self, key: &str) -> Option<EtagEntry> {
        let mut guard = self.state.lock().unwrap_or_else(|p| p.into_inner());
        let (entries, order) = &mut *guard;
        let entry = entries.get(key)?.clone();
        order.retain(|k| k != key);
        order.push_back(key.to_owned());
        Some(entry)
    }

    fn put(&self, key: &str, entry: EtagEntry) {
        let mut guard = self.state.lock().unwrap_or_else(|p| p.into_inner());
        let (entries, order) = &mut *guard;
        order.retain(|k| k != key);
        order.push_back(key.to_owned());
        entries.insert(key.to_owned(), entry);
        while entries.len() > self.limit {
            let Some(oldest) = order.pop_front() else {
                break;
            };
            entries.remove(&oldest);
        }
    }

    fn remove(&self, key: &str) {
        let mut guard = self.state.lock().unwrap_or_else(|p| p.into_inner());
        let (entries, order) = &mut *guard;
        entries.remove(key);
        order.retain(|k| k != key);
    }
}
