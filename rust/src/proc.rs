//! Typed CLI subprocess helper shared by Rust node agents.
//!
//! Every executor that shells out to a system CLI (docker, nft, nginx, wg, …)
//! repeats the same shape: spawn `program` with a fixed arg list, fail on a
//! non-zero exit with the trimmed stderr, and read stdout on success. This
//! centralises that shape so executors stay one line and the error contract is
//! uniform.
//!
//! INVARIANT: callers pass a fixed `program` + typed `args` slice — there is no
//! shell, no `sh -c`, no string interpolation of operator input into a command
//! line. That keeps the "no arbitrary shell" agent invariant intact.

use std::ffi::OsStr;
use std::process::Command;

/// Run `program args…` to completion, capturing output.
///
/// Returns the captured stdout (lossy UTF-8) on a zero exit, or the trimmed
/// stderr on a non-zero exit. A spawn failure (binary missing, no perms) is
/// reported as `"<program> exec failed: <io error>"`.
pub fn run_capture<S, A>(program: S, args: A) -> Result<String, String>
where
    S: AsRef<OsStr>,
    A: IntoIterator,
    A::Item: AsRef<OsStr>,
{
    let prog = program.as_ref().to_string_lossy().into_owned();
    let out = Command::new(&program)
        .args(args)
        .output()
        .map_err(|e| format!("{prog} exec failed: {e}"))?;
    if !out.status.success() {
        return Err(String::from_utf8_lossy(&out.stderr).trim().to_string());
    }
    Ok(String::from_utf8_lossy(&out.stdout).into_owned())
}

/// Like [`run_capture`] but discards stdout — for commands run only for their
/// effect (e.g. `nft add table`). Still surfaces the trimmed stderr on failure.
pub fn run_ok<S, A>(program: S, args: A) -> Result<(), String>
where
    S: AsRef<OsStr>,
    A: IntoIterator,
    A::Item: AsRef<OsStr>,
{
    run_capture(program, args).map(|_| ())
}
