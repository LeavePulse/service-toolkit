//! Tracing setup shared by Rust services.

use tracing_subscriber::EnvFilter;

/// Initialise the global `tracing` subscriber from a log-level/filter string
/// (e.g. `"info"` or a full `RUST_LOG`-style directive). An unparseable filter
/// falls back to `info` rather than failing service startup.
///
/// Call once, early in `main`, before emitting any spans.
pub fn init_tracing(log_level: &str) {
    let filter = EnvFilter::try_new(log_level).unwrap_or_else(|_| EnvFilter::new("info"));
    tracing_subscriber::fmt()
        .with_env_filter(filter)
        .with_target(true)
        .init();
}
