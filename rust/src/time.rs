//! Wall-clock helpers shared by Rust services.

use std::time::{SystemTime, UNIX_EPOCH};

/// Milliseconds since the Unix epoch as `i64` (saturates to 0 before 1970 /
/// on a clock error). Suits proto/JSON timestamp fields.
pub fn now_millis() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as i64)
        .unwrap_or(0)
}

/// Milliseconds since the Unix epoch as `u128` — for unsigned contexts such as
/// monotonic filename suffixes where the full range matters.
pub fn now_millis_u128() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0)
}
