"""Native performance-critical helpers, exposed under the service_toolkit namespace.

The implementation lives in the compiled ``service_toolkit_rust`` extension
(the ``rust/`` crate in this repo, replacing the old ``leavepulse_core``
package). It is an *optional* dependency: only services that need these helpers
install it via the ``service-toolkit[rust]`` extra, so the rest of the fleet
never pulls a Rust toolchain.

Import as::

    from service_toolkit import core as _core
    _core.normalize_motd(...)
"""

from __future__ import annotations

try:
    import service_toolkit_rust as _native
except ModuleNotFoundError as exc:  # pragma: no cover - import-time guard
    msg = (
        "service_toolkit.core requires the native extension. "
        "Install it with the 'rust' extra: service-toolkit[rust]."
    )
    raise ModuleNotFoundError(msg) from exc

# MOTD
normalize_motd = _native.normalize_motd
MotdMatcher = _native.MotdMatcher

# Segments
Sample = _native.Sample
Override = _native.Override
Interval = _native.Interval
State = _native.State
build_segment_intervals = _native.build_segment_intervals
resolve_state_at = _native.resolve_state_at
resolve_state_for_range = _native.resolve_state_for_range
build_bucket_state_map = _native.build_bucket_state_map
compute_effective_average = _native.compute_effective_average
compute_effective_average_stats = _native.compute_effective_average_stats

# Scoring
compute_scores_batch = _native.compute_scores_batch

# Text
normalize_sitemap_text = _native.normalize_sitemap_text
normalize_sitemap_texts_batch = _native.normalize_sitemap_texts_batch

# Import resolution
resolve_candidates = _native.resolve_candidates

# Constants
STATUS_SUSPECT = _native.STATUS_SUSPECT
STATUS_MAINTENANCE = _native.STATUS_MAINTENANCE

__all__ = [
    "MotdMatcher",
    "STATUS_MAINTENANCE",
    "STATUS_SUSPECT",
    "Interval",
    "Override",
    "Sample",
    "State",
    "build_bucket_state_map",
    "build_segment_intervals",
    "compute_effective_average",
    "compute_effective_average_stats",
    "compute_scores_batch",
    "normalize_motd",
    "normalize_sitemap_text",
    "normalize_sitemap_texts_batch",
    "resolve_candidates",
    "resolve_state_at",
    "resolve_state_for_range",
]
