//! service-toolkit Rust crate.
//!
//! Two independent consumer surfaces gated by Cargo features:
//!   * `python` (default) — PyO3 extension module with the performance-critical
//!     helpers (motd/segments/scoring/text/import_resolve). maturin builds it
//!     into the importable `service_toolkit_rust` module (replaces the former
//!     standalone `leavepulse_core` package). The `extension-module` feature
//!     adds the import link flag.
//!   * `grpc` — pure-Rust gRPC client helpers (channel + internal-token
//!     interceptor) for Rust consumers such as server-poller. No Python dep.

#[cfg(feature = "python")]
mod import_resolve;
#[cfg(feature = "python")]
mod motd;
#[cfg(feature = "python")]
mod scoring;
#[cfg(feature = "python")]
mod segments;
#[cfg(feature = "python")]
mod text;

#[cfg(feature = "grpc")]
pub mod grpc;

#[cfg(feature = "python")]
use pyo3::prelude::*;

/// Native extension module imported as ``service_toolkit_rust`` (replaces the
/// former standalone ``leavepulse_core`` package).
#[cfg(feature = "python")]
#[pymodule]
fn service_toolkit_rust(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // motd
    m.add_function(wrap_pyfunction!(motd::normalize_motd, m)?)?;
    m.add_class::<motd::MotdMatcher>()?;

    // segments — types
    m.add_class::<segments::Sample>()?;
    m.add_class::<segments::Override>()?;
    m.add_class::<segments::Interval>()?;
    m.add_class::<segments::State>()?;

    // segments — functions
    m.add_function(wrap_pyfunction!(segments::build_segment_intervals, m)?)?;
    m.add_function(wrap_pyfunction!(segments::resolve_state_at, m)?)?;
    m.add_function(wrap_pyfunction!(segments::resolve_state_for_range, m)?)?;
    m.add_function(wrap_pyfunction!(segments::build_bucket_state_map, m)?)?;
    m.add_function(wrap_pyfunction!(segments::compute_effective_average, m)?)?;
    m.add_function(wrap_pyfunction!(segments::compute_effective_average_stats, m)?)?;

    // scoring
    m.add_function(wrap_pyfunction!(scoring::compute_scores_batch, m)?)?;

    // text normalization
    m.add_function(wrap_pyfunction!(text::normalize_sitemap_text, m)?)?;
    m.add_function(wrap_pyfunction!(text::normalize_sitemap_texts_batch, m)?)?;

    // import resolution
    m.add_function(wrap_pyfunction!(import_resolve::resolve_candidates, m)?)?;

    // constants
    m.add("STATUS_SUSPECT", segments::STATUS_SUSPECT)?;
    m.add("STATUS_MAINTENANCE", segments::STATUS_MAINTENANCE)?;

    Ok(())
}
