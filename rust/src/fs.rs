//! Durable file replacement shared by node agents and desktop/mobile apps.
//!
//! Every writer that must never leave a torn file repeats the same shape:
//! write a sibling, `fsync` it, rename it over the destination, and `fsync`
//! the directory so the rename itself survives a power loss. The copies
//! drifted: some skipped the file sync, some the directory sync, some named the
//! sibling `<path>.tmp` (two writers then share one temporary), and one used
//! `with_extension("part")`, which maps `a.jar` and `a.zip` to the same name.
//! This module is that shape once.
//!
//! * The sibling lives in the destination's directory, so the rename never
//!   crosses a filesystem, and its name carries the pid and a per-process
//!   sequence; it is created with `create_new`, never reused.
//! * [`Access::Private`] creates the sibling `0600` BEFORE any byte is written,
//!   so a secret never exists under a wider mode, not even for a moment.
//! * The parent directory is synced on unix. `rename(2)` replaces the name
//!   atomically, but per `fsync(2)` syncing the file "does not necessarily
//!   ensure that the entry in the directory containing the file has also
//!   reached disk". Windows has no directory handle to sync; NTFS journals the
//!   rename itself.
//! * On Windows a rename over a file another process has open without
//!   `FILE_SHARE_DELETE` (an antivirus scan, the indexer) fails with a sharing
//!   or access error that clears on its own; the rename is retried briefly
//!   there before the error is returned.
//!
//! Directories are not created here: their mode is the caller's policy (a
//! state directory is `0700`, a game folder is not).

use std::ffi::OsString;
use std::fmt;
use std::fs::{self, File, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

use serde::Serialize;
use serde::de::DeserializeOwned;

static TEMPORARY_SEQUENCE: AtomicU64 = AtomicU64::new(0);

/// How many fresh sibling names are tried before giving up. A name is only
/// ever taken by a leftover of an earlier process with the same pid.
const TEMPORARY_ATTEMPTS: u32 = 16;

/// Who may read the written file. Applies on unix; elsewhere the file inherits
/// the directory's ACL whatever is asked.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Access {
    /// `0666` narrowed by the umask, as `File::create` would make it.
    #[default]
    Shared,
    /// Exactly `0600`, set before any byte is written. For keys, tokens and
    /// state that describes who may reach what.
    Private,
    /// Exactly `0755`, whatever the umask. For a binary that is run after it
    /// lands (self-update, a pinned tool).
    Executable,
}

impl Access {
    #[cfg(unix)]
    fn mode(self) -> Option<u32> {
        match self {
            Access::Shared => None,
            Access::Private => Some(0o600),
            Access::Executable => Some(0o755),
        }
    }
}

/// A fresh sibling name for *path*: `<path>.new-<pid>-<sequence>`.
pub fn temp_sibling(path: &Path) -> PathBuf {
    let mut name = path
        .file_name()
        .map(|name| name.to_os_string())
        .unwrap_or_else(|| OsString::from("file"));
    name.push(format!(
        ".new-{}-{}",
        std::process::id(),
        TEMPORARY_SEQUENCE.fetch_add(1, Ordering::Relaxed)
    ));
    path.with_file_name(name)
}

/// Create *path*, which must not exist, with *access* and nothing written.
fn open_new(path: &Path, access: Access) -> io::Result<File> {
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        if let Some(mode) = access.mode() {
            options.mode(mode);
        }
    }
    let file = options.open(path)?;
    #[cfg(unix)]
    if let Some(mode) = access.mode() {
        // The umask may have narrowed the open mode; the declared one is exact.
        use std::os::unix::fs::PermissionsExt;
        if let Err(error) = file.set_permissions(fs::Permissions::from_mode(mode)) {
            drop(file);
            let _ = fs::remove_file(path);
            return Err(error);
        }
    }
    #[cfg(not(unix))]
    let _ = access;
    Ok(file)
}

/// Create an empty sibling of *path* with *access*, for a caller that streams
/// into it and then hands it to [`persist`]. Returns the open file and its name.
pub fn create_temp(path: &Path, access: Access) -> io::Result<(File, PathBuf)> {
    let mut last = None;
    for _ in 0..TEMPORARY_ATTEMPTS {
        let temp = temp_sibling(path);
        match open_new(&temp, access) {
            Ok(file) => return Ok((file, temp)),
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => last = Some(error),
            Err(error) => return Err(error),
        }
    }
    Err(last.unwrap_or_else(|| io::Error::from(io::ErrorKind::AlreadyExists)))
}

/// Create *path* with *access* and *contents*, synced. Refuses a path that
/// already exists (`create_new`) rather than following an earlier existence
/// check into a race; a failed write removes what it created.
pub fn write_new(path: &Path, contents: &[u8], access: Access) -> io::Result<()> {
    let mut file = open_new(path, access)?;
    let result = file.write_all(contents).and_then(|()| file.sync_all());
    if result.is_err() {
        drop(file);
        let _ = fs::remove_file(path);
    }
    result
}

/// Move the synced sibling *temp* over *path* and make the rename durable.
/// The sibling is removed when the rename fails.
pub fn persist(temp: &Path, path: &Path) -> io::Result<()> {
    if let Err(error) = rename_replacing(temp, path) {
        let _ = fs::remove_file(temp);
        return Err(error);
    }
    sync_parent(path)
}

/// Atomically replace *path* with *contents*: a reader, or a reboot, sees the
/// old file or the new one, never a torn one.
pub fn replace(path: &Path, contents: &[u8], access: Access) -> io::Result<()> {
    let (mut file, temp) = create_temp(path, access)?;
    let written = file.write_all(contents).and_then(|()| file.sync_all());
    drop(file);
    if let Err(error) = written {
        let _ = fs::remove_file(&temp);
        return Err(error);
    }
    persist(&temp, path)
}

/// [`replace`] only when the bytes differ; returns whether it wrote.
///
/// An unreadable or absent destination counts as different, so the read can
/// never mask a needed write. With equal bytes a `Private` or `Executable`
/// destination still gets its declared mode back: a file left by an older
/// writer interrupted between its write and its chmod must not keep the wider
/// mode just because its content is right.
pub fn replace_if_changed(path: &Path, contents: &[u8], access: Access) -> io::Result<bool> {
    if fs::read(path).is_ok_and(|existing| existing == contents) {
        #[cfg(unix)]
        if let Some(mode) = access.mode() {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(path, fs::Permissions::from_mode(mode))?;
        }
        return Ok(false);
    }
    replace(path, contents, access)?;
    Ok(true)
}

#[cfg(windows)]
fn rename_replacing(from: &Path, to: &Path) -> io::Result<()> {
    // ERROR_ACCESS_DENIED, ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION: the
    // destination is open elsewhere without FILE_SHARE_DELETE, usually briefly.
    const TRANSIENT: [i32; 3] = [5, 32, 33];
    const ATTEMPTS: u32 = 6;
    let mut delay = std::time::Duration::from_millis(20);
    let mut attempt = 1;
    loop {
        match fs::rename(from, to) {
            Ok(()) => return Ok(()),
            Err(error)
                if attempt < ATTEMPTS
                    && error
                        .raw_os_error()
                        .is_some_and(|code| TRANSIENT.contains(&code)) =>
            {
                std::thread::sleep(delay);
                delay *= 2;
                attempt += 1;
            }
            Err(error) => return Err(error),
        }
    }
}

#[cfg(not(windows))]
fn rename_replacing(from: &Path, to: &Path) -> io::Result<()> {
    fs::rename(from, to)
}

/// Make a rename into *path*'s directory durable (unix; a no-op elsewhere).
pub fn sync_parent(path: &Path) -> io::Result<()> {
    #[cfg(unix)]
    {
        let parent = path
            .parent()
            .filter(|parent| !parent.as_os_str().is_empty())
            .unwrap_or_else(|| Path::new("."));
        File::open(parent)?.sync_all()
    }
    #[cfg(not(unix))]
    {
        let _ = path;
        Ok(())
    }
}

/// Why a JSON state file could not be loaded or saved. Every variant names the
/// file.
#[derive(Debug)]
pub enum StateError {
    /// The file exists (or may) but could not be read.
    Read {
        path: PathBuf,
        source: io::Error,
    },
    /// The file was read but is not a valid record. Never turned into a
    /// default: a state file that cannot be trusted is not a fresh one.
    Parse {
        path: PathBuf,
        source: serde_json::Error,
    },
    Encode {
        path: PathBuf,
        source: serde_json::Error,
    },
    Write {
        path: PathBuf,
        source: io::Error,
    },
}

impl fmt::Display for StateError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            StateError::Read { path, source } => {
                write!(f, "cannot read {}: {source}", path.display())
            }
            StateError::Parse { path, source } => {
                write!(f, "{} is unreadable: {source}", path.display())
            }
            StateError::Encode { path, source } => {
                write!(f, "cannot encode {}: {source}", path.display())
            }
            StateError::Write { path, source } => {
                write!(f, "cannot write {}: {source}", path.display())
            }
        }
    }
}

impl std::error::Error for StateError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            StateError::Read { source, .. } | StateError::Write { source, .. } => Some(source),
            StateError::Parse { source, .. } | StateError::Encode { source, .. } => Some(source),
        }
    }
}

/// The JSON record at *path*, or `None` when there is no file. A file that
/// exists and does not parse is an error, not an absence.
pub fn load_json<T: DeserializeOwned>(path: &Path) -> Result<Option<T>, StateError> {
    match fs::read(path) {
        Ok(bytes) => serde_json::from_slice(&bytes)
            .map(Some)
            .map_err(|source| StateError::Parse {
                path: path.to_path_buf(),
                source,
            }),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(None),
        Err(source) => Err(StateError::Read {
            path: path.to_path_buf(),
            source,
        }),
    }
}

/// Like [`load_json`], with `T::default()` for a missing file only.
pub fn load_json_or_default<T: DeserializeOwned + Default>(path: &Path) -> Result<T, StateError> {
    load_json(path).map(Option::unwrap_or_default)
}

/// Save *value* as compact JSON at *path* with [`replace_if_changed`];
/// returns whether the file was written.
pub fn save_json<T: Serialize + ?Sized>(
    path: &Path,
    value: &T,
    access: Access,
) -> Result<bool, StateError> {
    let bytes = serde_json::to_vec(value).map_err(|source| StateError::Encode {
        path: path.to_path_buf(),
        source,
    })?;
    replace_if_changed(path, &bytes, access).map_err(|source| StateError::Write {
        path: path.to_path_buf(),
        source,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeMap;

    fn scratch(name: &str) -> PathBuf {
        static N: AtomicU64 = AtomicU64::new(0);
        let dir = std::env::temp_dir().join(format!(
            "service-toolkit-fs-{}-{}-{name}",
            std::process::id(),
            N.fetch_add(1, Ordering::Relaxed)
        ));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn entries(dir: &Path) -> Vec<String> {
        let mut names: Vec<String> = fs::read_dir(dir)
            .unwrap()
            .map(|entry| entry.unwrap().file_name().to_string_lossy().into_owned())
            .collect();
        names.sort();
        names
    }

    #[test]
    fn replace_lands_the_content_and_leaves_no_sibling() {
        let dir = scratch("replace");
        let path = dir.join("state.json");
        replace(&path, b"first", Access::Shared).unwrap();
        replace(&path, b"second", Access::Shared).unwrap();
        assert_eq!(fs::read(&path).unwrap(), b"second");
        assert_eq!(entries(&dir), ["state.json"]);
        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn siblings_are_unique_and_keep_the_extension_apart() {
        let a = temp_sibling(Path::new("dir/a.jar"));
        let b = temp_sibling(Path::new("dir/a.zip"));
        let c = temp_sibling(Path::new("dir/a.jar"));
        assert_ne!(a, b);
        assert_ne!(a, c);
        assert_eq!(a.parent(), Some(Path::new("dir")));
        assert!(a.to_string_lossy().contains("a.jar.new-"));
    }

    #[test]
    fn replace_if_changed_writes_only_different_bytes() {
        let dir = scratch("changed");
        let path = dir.join("value");
        assert!(replace_if_changed(&path, b"one", Access::Shared).unwrap());
        assert!(!replace_if_changed(&path, b"one", Access::Shared).unwrap());
        assert!(replace_if_changed(&path, b"two", Access::Shared).unwrap());
        assert_eq!(fs::read(&path).unwrap(), b"two");
        assert_eq!(entries(&dir), ["value"]);
        fs::remove_dir_all(&dir).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn an_unchanged_write_does_not_touch_the_file() {
        use std::os::unix::fs::MetadataExt;
        let dir = scratch("inode");
        let path = dir.join("value");
        replace_if_changed(&path, b"same", Access::Shared).unwrap();
        let before = fs::metadata(&path).unwrap().ino();
        replace_if_changed(&path, b"same", Access::Shared).unwrap();
        assert_eq!(fs::metadata(&path).unwrap().ino(), before);
        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn write_new_refuses_an_existing_path() {
        let dir = scratch("new");
        let path = dir.join("key");
        write_new(&path, b"secret", Access::Private).unwrap();
        let error = write_new(&path, b"other", Access::Private).unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::AlreadyExists);
        assert_eq!(fs::read(&path).unwrap(), b"secret");
        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn persist_moves_a_streamed_sibling_over_the_destination() {
        let dir = scratch("persist");
        let path = dir.join("bundle.zip");
        fs::write(&path, b"old").unwrap();
        let (mut file, temp) = create_temp(&path, Access::Shared).unwrap();
        file.write_all(b"new").unwrap();
        file.sync_all().unwrap();
        drop(file);
        persist(&temp, &path).unwrap();
        assert_eq!(fs::read(&path).unwrap(), b"new");
        assert_eq!(entries(&dir), ["bundle.zip"]);
        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn a_failed_persist_removes_the_sibling() {
        let dir = scratch("persist-fail");
        let path = dir.join("missing-dir").join("file");
        let (file, temp) = create_temp(&dir.join("file"), Access::Shared).unwrap();
        drop(file);
        assert!(persist(&temp, &path).is_err());
        assert!(!temp.exists());
        fs::remove_dir_all(&dir).unwrap();
    }

    #[cfg(unix)]
    fn mode(path: &Path) -> u32 {
        use std::os::unix::fs::PermissionsExt;
        fs::metadata(path).unwrap().permissions().mode() & 0o7777
    }

    #[cfg(unix)]
    #[test]
    fn private_files_are_owner_only_and_an_equal_write_repairs_the_mode() {
        use std::os::unix::fs::PermissionsExt;
        let dir = scratch("private");
        let path = dir.join("key");
        write_new(&path, b"secret", Access::Private).unwrap();
        assert_eq!(mode(&path), 0o600);

        let replaced = dir.join("token");
        assert!(replace_if_changed(&replaced, b"secret", Access::Private).unwrap());
        assert_eq!(mode(&replaced), 0o600);
        fs::set_permissions(&replaced, fs::Permissions::from_mode(0o644)).unwrap();
        assert!(!replace_if_changed(&replaced, b"secret", Access::Private).unwrap());
        assert_eq!(mode(&replaced), 0o600, "an equal write left the wider mode");
        fs::remove_dir_all(&dir).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn executables_get_exactly_0755() {
        let dir = scratch("exec");
        let path = dir.join("tool");
        replace(&path, b"#!/bin/sh\n", Access::Executable).unwrap();
        assert_eq!(mode(&path), 0o755);
        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn json_state_round_trips_and_reports_whether_it_wrote() {
        let dir = scratch("json");
        let path = dir.join("state.json");
        let mut state = BTreeMap::new();
        state.insert("generation".to_string(), 7u64);
        assert!(save_json(&path, &state, Access::Private).unwrap());
        assert!(!save_json(&path, &state, Access::Private).unwrap());
        let loaded: Option<BTreeMap<String, u64>> = load_json(&path).unwrap();
        assert_eq!(loaded, Some(state));
        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn a_missing_state_file_is_none_or_the_default() {
        let dir = scratch("json-missing");
        let path = dir.join("absent.json");
        let loaded: Option<BTreeMap<String, u64>> = load_json(&path).unwrap();
        assert_eq!(loaded, None);
        let fresh: BTreeMap<String, u64> = load_json_or_default(&path).unwrap();
        assert!(fresh.is_empty());
        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn a_corrupt_state_file_is_an_error_not_a_default() {
        let dir = scratch("json-corrupt");
        let path = dir.join("state.json");
        fs::write(&path, b"{\"generation\": ").unwrap();
        let error = load_json_or_default::<BTreeMap<String, u64>>(&path).unwrap_err();
        assert!(matches!(error, StateError::Parse { .. }), "{error:?}");
        assert!(
            error.to_string().contains("state.json is unreadable"),
            "{error}"
        );
        fs::remove_dir_all(&dir).unwrap();
    }
}
