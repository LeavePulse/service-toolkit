//! Environment-variable parsing helpers shared by Rust services.
//!
//! Every service config previously re-declared this same family of typed env
//! readers (str/u64/usize/f64/u16/bool + optional). They live here once so a
//! new service's `config.rs` is just field assignments, not boilerplate.
//!
//! Each reader returns the parsed value or `default` when the var is unset or
//! unparseable — services treat a malformed env value as "use the default"
//! rather than crashing on boot.

use std::env;
use std::str::FromStr;

/// String var, or `default` when unset.
pub fn str(key: &str, default: &str) -> String {
    env::var(key).unwrap_or_else(|_| default.to_string())
}

/// String var, or `None` when unset or empty. Use for optional tokens/URLs
/// where an empty string should be treated the same as absent.
pub fn str_opt(key: &str) -> Option<String> {
    env::var(key).ok().filter(|s| !s.is_empty())
}

/// Parse any `FromStr` value from a var, falling back to `default`.
pub fn parse<T: FromStr>(key: &str, default: T) -> T {
    env::var(key)
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(default)
}

/// `u64` var, or `default`.
pub fn u64(key: &str, default: u64) -> u64 {
    parse(key, default)
}

/// `usize` var, or `default`.
pub fn usize(key: &str, default: usize) -> usize {
    parse(key, default)
}

/// `f64` var, or `default`.
pub fn f64(key: &str, default: f64) -> f64 {
    parse(key, default)
}

/// `u16` var, or `default`.
pub fn u16(key: &str, default: u16) -> u16 {
    parse(key, default)
}

/// Boolean var. Truthy: `true`/`1`/`yes`/`on` (case-insensitive). Anything
/// else parses as `false`; unset yields `default`.
pub fn bool(key: &str, default: bool) -> bool {
    env::var(key)
        .ok()
        .map(|v| matches!(v.to_ascii_lowercase().as_str(), "true" | "1" | "yes" | "on"))
        .unwrap_or(default)
}
