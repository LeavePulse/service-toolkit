//! `flock(2)` locks between processes: named file locks and a process
//! singleton (unix only).
//!
//! The lock belongs to the open file description, so it holds against another
//! process and against a second `open` in this one, and the kernel releases it
//! when the holder's descriptor closes, including when the holder dies: no PID
//! file, no staleness test, no takeover timeout. std opens every file with
//! `O_CLOEXEC`, so the descriptor does not outlive the holder in a child it
//! runs. Lock files are created `0600` and never followed through a symlink
//! (`O_NOFOLLOW`), and they are never removed: a lock on an unlinked file
//! excludes nobody, since the next process would create a new file and lock
//! that.
//!
//! These coordinate cooperating processes. They stop nobody who does not take
//! the lock, and they are no defence against a hostile root.

use std::fmt;
use std::fs::{self, DirBuilder, File, Metadata, OpenOptions, TryLockError};
use std::io;
use std::os::unix::fs::{DirBuilderExt, MetadataExt, OpenOptionsExt};
use std::path::{Path, PathBuf};

/// Exit status for "another instance holds the lock": `EX_TEMPFAIL`, distinct
/// so a unit can list it in `RestartPreventExitStatus=`.
pub const CONTENTION_EXIT: i32 = 75;

/// A held named lock; dropping it releases it.
#[derive(Debug)]
pub struct FileLock {
    file: File,
    path: PathBuf,
}

impl FileLock {
    pub fn path(&self) -> &Path {
        &self.path
    }
}

impl Drop for FileLock {
    /// Unlock explicitly before the descriptor closes. Closing alone releases
    /// the lock only once every descriptor on the open file description is
    /// gone, and a child another thread is spawning holds a copy until its
    /// `exec`: the lock would outlive the holder by that window.
    fn drop(&mut self) {
        let _ = self.file.unlock();
    }
}

fn with_path(path: &Path, error: io::Error) -> io::Error {
    io::Error::new(error.kind(), format!("{}: {error}", path.display()))
}

fn open_lock_file(path: &Path) -> io::Result<File> {
    if let Some(dir) = path.parent().filter(|d| !d.as_os_str().is_empty()) {
        fs::create_dir_all(dir).map_err(|e| with_path(dir, e))?;
    }
    OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW)
        .open(path)
        .map_err(|e| with_path(path, e))
}

/// Take the lock at *path*, waiting for a holder to finish. Missing parent
/// directories are created.
pub fn hold(path: &Path) -> io::Result<FileLock> {
    let file = open_lock_file(path)?;
    file.lock().map_err(|e| with_path(path, e))?;
    Ok(FileLock {
        file,
        path: path.to_path_buf(),
    })
}

/// Take the lock at *path* without waiting; `None` when someone holds it.
pub fn try_hold(path: &Path) -> io::Result<Option<FileLock>> {
    let file = open_lock_file(path)?;
    match file.try_lock() {
        Ok(()) => Ok(Some(FileLock {
            file,
            path: path.to_path_buf(),
        })),
        Err(TryLockError::WouldBlock) => Ok(None),
        Err(TryLockError::Error(e)) => Err(with_path(path, e)),
    }
}

/// The held singleton lock. Dropping it (or the process ending) releases it.
#[derive(Debug)]
pub struct Guard {
    file: File,
    path: PathBuf,
}

impl Guard {
    pub fn path(&self) -> &Path {
        &self.path
    }
}

impl Drop for Guard {
    fn drop(&mut self) {
        let _ = self.file.unlock();
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Directory,
    File,
}

/// What the check needs to know about a path, separated from `Metadata` so the
/// rules can be tested with owners a non-root test cannot create.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Facts {
    pub is_symlink: bool,
    pub is_dir: bool,
    pub is_file: bool,
    pub uid: u32,
    pub mode: u32,
}

impl Facts {
    pub fn of(meta: &Metadata) -> Self {
        let kind = meta.file_type();
        Facts {
            is_symlink: kind.is_symlink(),
            is_dir: kind.is_dir(),
            is_file: kind.is_file(),
            uid: meta.uid(),
            mode: meta.mode() & 0o7777,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Unsafe {
    Symlink,
    NotA(Kind),
    Owner { found: u32, expected: u32 },
    Mode { found: u32, expected: u32 },
}

impl fmt::Display for Unsafe {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Unsafe::Symlink => write!(f, "is a symlink"),
            Unsafe::NotA(Kind::Directory) => write!(f, "is not a directory"),
            Unsafe::NotA(Kind::File) => write!(f, "is not a regular file"),
            Unsafe::Owner { found, expected } => write!(
                f,
                "is owned by uid {found}, not by this process (uid {expected})"
            ),
            Unsafe::Mode { found, expected } => write!(
                f,
                "has mode {found:04o}; it must be exactly {expected:04o} \
                 (owner only, no special bits)"
            ),
        }
    }
}

/// The mode a trusted directory or lock file must have, in the low 12 bits.
pub fn expected_mode(kind: Kind) -> u32 {
    match kind {
        Kind::Directory => 0o700,
        Kind::File => 0o600,
    }
}

/// A singleton location is trusted only if it is the expected kind of object,
/// not a symlink, owned by the effective user, and its permission bits
/// (`mode & 0o7777`) are exactly [`expected_mode`]. A wider location is one
/// someone else set up, or can see into.
pub fn check(kind: Kind, facts: &Facts, euid: u32) -> Result<(), Unsafe> {
    if facts.is_symlink {
        return Err(Unsafe::Symlink);
    }
    let right_kind = match kind {
        Kind::Directory => facts.is_dir,
        Kind::File => facts.is_file,
    };
    if !right_kind {
        return Err(Unsafe::NotA(kind));
    }
    if facts.uid != euid {
        return Err(Unsafe::Owner {
            found: facts.uid,
            expected: euid,
        });
    }
    let expected = expected_mode(kind);
    if facts.mode & 0o7777 != expected {
        return Err(Unsafe::Mode {
            found: facts.mode & 0o7777,
            expected,
        });
    }
    Ok(())
}

#[derive(Debug)]
pub enum AcquireError {
    /// Another open file description holds the lock.
    Held(PathBuf),
    Unsafe(PathBuf, Unsafe),
    Io(PathBuf, io::Error),
}

impl fmt::Display for AcquireError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            AcquireError::Held(path) => write!(f, "another process holds {}", path.display()),
            AcquireError::Unsafe(path, why) => {
                write!(f, "refusing lock location {}: it {why}", path.display())
            }
            AcquireError::Io(path, e) => write!(f, "cannot use lock {}: {e}", path.display()),
        }
    }
}

impl std::error::Error for AcquireError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            AcquireError::Io(_, e) => Some(e),
            AcquireError::Held(_) | AcquireError::Unsafe(..) => None,
        }
    }
}

pub fn euid() -> u32 {
    // SAFETY: geteuid takes no arguments, touches no memory and cannot fail.
    unsafe { libc::geteuid() }
}

/// Create (`0700`) or validate *dir*, open *dir*/*file_name* (`0600`,
/// `O_NOFOLLOW`), check it through the opened descriptor, and take the lock
/// without waiting. The directory is checked by `lstat` just before, which
/// leaves a window only for whoever can rewrite its parent.
pub fn acquire(dir: &Path, file_name: &str) -> Result<Guard, AcquireError> {
    let euid = euid();

    match DirBuilder::new().mode(0o700).create(dir) {
        Ok(()) => {}
        Err(e) if e.kind() == io::ErrorKind::AlreadyExists => {}
        Err(e) => return Err(AcquireError::Io(dir.to_path_buf(), e)),
    }
    let meta = fs::symlink_metadata(dir).map_err(|e| AcquireError::Io(dir.to_path_buf(), e))?;
    check(Kind::Directory, &Facts::of(&meta), euid)
        .map_err(|why| AcquireError::Unsafe(dir.to_path_buf(), why))?;

    let path = dir.join(file_name);
    let file = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW)
        .open(&path)
        .map_err(|e| {
            if e.raw_os_error() == Some(libc::ELOOP) {
                AcquireError::Unsafe(path.clone(), Unsafe::Symlink)
            } else {
                AcquireError::Io(path.clone(), e)
            }
        })?;
    let meta = file
        .metadata()
        .map_err(|e| AcquireError::Io(path.clone(), e))?;
    check(Kind::File, &Facts::of(&meta), euid)
        .map_err(|why| AcquireError::Unsafe(path.clone(), why))?;

    match file.try_lock() {
        Ok(()) => Ok(Guard { file, path }),
        Err(TryLockError::WouldBlock) => Err(AcquireError::Held(path)),
        Err(TryLockError::Error(e)) => Err(AcquireError::Io(path, e)),
    }
}

/// [`acquire`] or end the process: [`CONTENTION_EXIT`] when another instance
/// holds the lock, `1` on any other refusal. Written to stderr, since nothing
/// else (logging included) is expected to be set up yet. Keep the guard for
/// the life of the process.
pub fn hold_or_exit(dir: &Path, file_name: &str, program: &str) -> Guard {
    match acquire(dir, file_name) {
        Ok(guard) => guard,
        Err(e @ AcquireError::Held(_)) => {
            eprintln!("{program}: {e}; not starting a second {program}");
            std::process::exit(CONTENTION_EXIT);
        }
        Err(e) => {
            eprintln!("{program}: {e}");
            std::process::exit(1);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::{PermissionsExt, symlink};
    use std::sync::atomic::{AtomicU32, Ordering};

    const LOCK_FILE: &str = "test.lock";

    fn scratch(name: &str) -> PathBuf {
        static N: AtomicU32 = AtomicU32::new(0);
        let dir = std::env::temp_dir().join(format!(
            "service-toolkit-lock-{}-{}-{name}",
            std::process::id(),
            N.fetch_add(1, Ordering::Relaxed)
        ));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn mode(path: &Path) -> u32 {
        fs::metadata(path).unwrap().permissions().mode() & 0o7777
    }

    fn good(kind: Kind, mode: u32) -> Facts {
        Facts {
            is_symlink: false,
            is_dir: kind == Kind::Directory,
            is_file: kind == Kind::File,
            uid: 1000,
            mode,
        }
    }

    #[test]
    fn a_held_lock_excludes_another_open_until_dropped() {
        let base = scratch("excl");
        let path = base.join("nested").join("x.lock");
        let held = hold(&path).unwrap();
        assert!(try_hold(&path).unwrap().is_none());
        drop(held);
        assert!(try_hold(&path).unwrap().is_some());
        fs::remove_dir_all(&base).unwrap();
    }

    #[test]
    fn a_dropped_lock_is_released_while_a_copy_of_its_descriptor_lives() {
        let base = scratch("copy");
        let path = base.join("x.lock");
        let held = hold(&path).unwrap();
        let copy = held.file.try_clone().unwrap();
        drop(held);
        assert!(try_hold(&path).unwrap().is_some());
        drop(copy);
        fs::remove_dir_all(&base).unwrap();
    }

    #[test]
    fn a_named_lock_file_is_private_and_never_followed() {
        let base = scratch("named");
        let path = base.join("x.lock");
        let held = hold(&path).unwrap();
        assert_eq!(held.path(), path);
        assert_eq!(mode(&path), 0o600);
        drop(held);

        symlink(base.join("elsewhere"), base.join("y.lock")).unwrap();
        assert!(hold(&base.join("y.lock")).is_err());
        assert!(!base.join("elsewhere").exists());
        fs::remove_dir_all(&base).unwrap();
    }

    #[test]
    fn check_accepts_owner_only_layout() {
        assert_eq!(
            check(Kind::Directory, &good(Kind::Directory, 0o700), 1000),
            Ok(())
        );
        assert_eq!(check(Kind::File, &good(Kind::File, 0o600), 1000), Ok(()));
    }

    #[test]
    fn check_refuses_another_owner() {
        for kind in [Kind::Directory, Kind::File] {
            assert_eq!(
                check(kind, &good(kind, expected_mode(kind)), 0),
                Err(Unsafe::Owner {
                    found: 1000,
                    expected: 0
                })
            );
        }
    }

    #[test]
    fn check_refuses_any_mode_but_owner_only() {
        let cases = [
            (
                Kind::Directory,
                &[
                    0o755, 0o750, 0o710, 0o701, 0o777, 0o1700, 0o2700, 0o4700, 0o600,
                ][..],
            ),
            (
                Kind::File,
                &[
                    0o644, 0o640, 0o604, 0o660, 0o4600, 0o2600, 0o1600, 0o700, 0o400,
                ][..],
            ),
        ];
        for (kind, modes) in cases {
            for &mode in modes {
                assert_eq!(
                    check(kind, &good(kind, mode), 1000),
                    Err(Unsafe::Mode {
                        found: mode,
                        expected: expected_mode(kind)
                    }),
                    "{kind:?} {mode:o}"
                );
            }
        }
        let why = check(Kind::Directory, &good(Kind::Directory, 0o755), 1000).unwrap_err();
        assert_eq!(
            why.to_string(),
            "has mode 0755; it must be exactly 0700 (owner only, no special bits)"
        );
    }

    #[test]
    fn check_refuses_symlink_and_wrong_kind() {
        let mut link = good(Kind::Directory, 0o700);
        link.is_symlink = true;
        link.is_dir = false;
        assert_eq!(check(Kind::Directory, &link, 1000), Err(Unsafe::Symlink));
        assert_eq!(
            check(Kind::Directory, &good(Kind::File, 0o700), 1000),
            Err(Unsafe::NotA(Kind::Directory))
        );
        assert_eq!(
            check(Kind::File, &good(Kind::Directory, 0o600), 1000),
            Err(Unsafe::NotA(Kind::File))
        );
    }

    #[test]
    fn acquire_creates_a_private_layout_and_refuses_a_second_holder() {
        let base = scratch("acquire");
        let dir = base.join("lock");
        let guard = acquire(&dir, LOCK_FILE).expect("first acquire");
        assert_eq!(mode(&dir), 0o700);
        assert_eq!(mode(&dir.join(LOCK_FILE)), 0o600);
        match acquire(&dir, LOCK_FILE) {
            Err(AcquireError::Held(path)) => assert_eq!(path, dir.join(LOCK_FILE)),
            other => panic!("expected Held, got {other:?}"),
        }
        drop(guard);
        drop(acquire(&dir, LOCK_FILE).expect("released on drop"));
        fs::remove_dir_all(&base).unwrap();
    }

    #[test]
    fn acquire_refuses_symlinks() {
        let base = scratch("links");
        let real = base.join("real");
        fs::create_dir(&real).unwrap();
        fs::set_permissions(&real, fs::Permissions::from_mode(0o700)).unwrap();
        let link = base.join("lock");
        symlink(&real, &link).unwrap();
        match acquire(&link, LOCK_FILE) {
            Err(AcquireError::Unsafe(path, Unsafe::Symlink)) => assert_eq!(path, link),
            other => panic!("expected symlink refusal, got {other:?}"),
        }
        assert!(!real.join(LOCK_FILE).exists());

        let target = base.join("elsewhere");
        fs::write(&target, b"").unwrap();
        symlink(&target, real.join(LOCK_FILE)).unwrap();
        match acquire(&real, LOCK_FILE) {
            Err(AcquireError::Unsafe(_, Unsafe::Symlink)) => {}
            other => panic!("expected symlink refusal, got {other:?}"),
        }
        fs::remove_dir_all(&base).unwrap();
    }

    #[test]
    fn acquire_refuses_an_existing_wider_dir_or_file() {
        let base = scratch("wider");
        let dir = base.join("lock");
        fs::create_dir(&dir).unwrap();
        fs::set_permissions(&dir, fs::Permissions::from_mode(0o755)).unwrap();
        match acquire(&dir, LOCK_FILE) {
            Err(e @ AcquireError::Unsafe(_, Unsafe::Mode { .. })) => {
                assert!(e.to_string().contains("must be exactly 0700"), "{e}")
            }
            other => panic!("expected mode refusal, got {other:?}"),
        }
        fs::set_permissions(&dir, fs::Permissions::from_mode(0o700)).unwrap();
        let file = dir.join(LOCK_FILE);
        fs::write(&file, b"").unwrap();
        fs::set_permissions(&file, fs::Permissions::from_mode(0o644)).unwrap();
        match acquire(&dir, LOCK_FILE) {
            Err(e @ AcquireError::Unsafe(_, Unsafe::Mode { .. })) => {
                assert!(e.to_string().contains("must be exactly 0600"), "{e}")
            }
            other => panic!("expected mode refusal, got {other:?}"),
        }
        fs::remove_dir_all(&base).unwrap();
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn the_lock_descriptor_is_not_inherited_by_children() {
        let base = scratch("cloexec");
        let dir = base.join("lock");
        let guard = acquire(&dir, LOCK_FILE).unwrap();
        let needle = guard.path().to_str().unwrap().to_string();
        let run = |script: &str| {
            let out = std::process::Command::new("sh")
                .arg("-c")
                .arg(script)
                .output()
                .unwrap();
            assert!(out.status.success(), "{out:?}");
            String::from_utf8(out.stdout).unwrap()
        };
        let opened = run(&format!("exec 9<'{needle}'; ls -l /proc/self/fd"));
        assert!(opened.contains(&needle), "control failed: {opened}");
        let listing = run("ls -l /proc/self/fd");
        assert!(!listing.contains(&needle), "lock fd leaked:\n{listing}");
        drop(guard);
        fs::remove_dir_all(&base).unwrap();
    }
}
