"""Fetch / refresh the GeoLite2 City mmdb at container start.

Downloads the ready-to-use ``GeoLite2-City.mmdb`` from the P3TERX/GeoLite.mmdb
mirror (a public, auto-updated GitHub mirror of MaxMind's GeoLite2 — no license
key needed) into ``GEOIP_DATA_DIR``. Designed to run before the app boots; it is
a best-effort no-op when:

  * downloading is disabled (``GEOIP_DOWNLOAD_ENABLED=false``), or
  * the network is unreachable but a previously fetched mmdb exists, or
  * the local copy is still fresh (refreshed within ``GEOIP_MAX_AGE_DAYS``).

Any unexpected error is swallowed with a log line. Every caller is written to
work without the database — a session falls back to showing the raw IP, an
enrolment list to the order it already had — so a download that fails must
never be a failed boot.

This is a boot-time entrypoint that runs as its own ``python -m`` invocation
*before* the app (and thus ``settings``/structlog) is wired up, so it reads its
config straight from the environment.

Source data is Copyright (c) MaxMind, Inc., distributed by the mirror under the
GeoLite2 EULA / CC BY-SA 4.0.
"""
# noqa: archlint=env-access

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import httpx

logging.basicConfig(level=logging.INFO, format="[fetch_geoip] %(message)s")
logger = logging.getLogger("service_toolkit.geo.fetch")

DEFAULT_URL = (
    "https://raw.githubusercontent.com/P3TERX/GeoLite.mmdb/download/GeoLite2-City.mmdb"
)


def _log(msg: str) -> None:
    logger.info(msg)


def _enabled(raw: str) -> bool:
    return raw.strip().lower() not in {"false", "0", "no", "off"}


def main() -> int:
    if not _enabled(os.getenv("GEOIP_DOWNLOAD_ENABLED", "true")):
        _log("GEOIP_DOWNLOAD_ENABLED is false; skipping GeoIP download")
        return 0

    url = os.getenv("GEOIP_CITY_DB_URL", DEFAULT_URL).strip()
    data_dir = Path(os.getenv("GEOIP_DATA_DIR", "/data/geoip"))
    filename = os.getenv("GEOIP_CITY_DB_FILENAME", "GeoLite2-City.mmdb")
    max_age_days = float(os.getenv("GEOIP_MAX_AGE_DAYS", "30"))

    target = data_dir / filename

    # Skip if a recent copy already exists.
    if target.is_file():
        age_days = (time.time() - target.stat().st_mtime) / 86400
        if age_days < max_age_days:
            _log(f"existing mmdb is {age_days:.1f}d old (< {max_age_days}d); skipping")
            return 0

    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _log(f"cannot create {data_dir} ({exc}); geoip disabled until next start")
        return 0

    tmp = target.with_suffix(target.suffix + ".tmp")
    try:
        with httpx.stream("GET", url, timeout=120.0, follow_redirects=True) as resp:
            resp.raise_for_status()
            with tmp.open("wb") as fh:
                for chunk in resp.iter_bytes(chunk_size=1 << 16):
                    fh.write(chunk)
        tmp.replace(target)  # atomic swap so readers never see a partial file
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        if target.is_file():
            _log(f"download failed ({exc}); keeping existing mmdb")
            return 0
        _log(f"download failed ({exc}); geoip disabled until next start")
        return 0

    _log(f"wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
