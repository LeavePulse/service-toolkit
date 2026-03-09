from __future__ import annotations

from pathlib import Path

from service_toolkit.dev.arch_linter import check_file, check_observability


def test_check_observability_accepts_toolkit_health_and_prometheus(
    tmp_path: Path,
) -> None:
    (tmp_path / "main.py").write_text(
        "\n".join(
            [
                "from service_toolkit import HealthController",
                "from service_toolkit.observability.prometheus import build_prometheus_instrumentation",
                "from service_toolkit.observability.tracing import setup_tracing",
                "PrometheusMiddleware, metrics_endpoint = build_prometheus_instrumentation(service_name='x')",
                "OpenTelemetryMiddleware = setup_tracing(service_name='x')",
                "route_handlers = [HealthController, metrics_endpoint]",
            ]
        ),
        encoding="utf-8",
    )

    assert check_observability(tmp_path) == []


def test_check_file_ignores_non_cache_locks_and_waiting_loops(tmp_path: Path) -> None:
    file_path = tmp_path / "sample.py"
    file_path.write_text(
        "\n".join(
            [
                "import asyncio",
                "",
                "_refresh_lock = asyncio.Lock()",
                "",
                "async def worker(socket):",
                "    while True:",
                "        await socket.receive_json()",
                "",
            ]
        ),
        encoding="utf-8",
    )

    assert check_file(file_path, None) == []
