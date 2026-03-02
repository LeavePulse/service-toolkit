# Service Toolkit

Reusable infrastructure helpers for LeavePulse services. Currently provides:

- Prometheus instrumentation factory (`build_prometheus_instrumentation`) that exposes a middleware and metrics endpoint tailored per service.
- Standard logging configuration builder (`build_standard_logging_config`) with health/metrics suppression support (defaults can be overridden).
- Request-scoped logging context middleware (`RequestContextLoggingMiddleware`) with `request_id`, `trace_id`, and `user_id` fields.
- SQLAlchemy slow-query logger installer (`install_slow_query_logging`) for unified DB observability.
- OpenTelemetry tracing bootstrap (`setup_tracing`) for distributed traces (OTLP export + auto-instrumentation for ASGI/httpx/sqlalchemy/redis).
- Simple health-check controller for Litestar applications (`HealthController`).
- Snowflake ID generation helpers (configurable epoch/node setup).
- A lightweight async NATS client wrapper (`NATSClient`) with convenience configuration.
- Redis-backed request rate limiting helpers (`enforce_request_rate_limit`, `rate_limited_request`).
- Async lookup cache with TTL, in-flight deduplication, and concurrency limits (`AsyncLookupCache`).
- (Extensible) space for other shared service utilities.

> **Note**
> Install extras as needed:
> - `pip install service-toolkit[nats]` for NATS helpers
> - `pip install service-toolkit[redis]` for Redis helpers
> - `pip install service-toolkit[tracing]` for OpenTelemetry helpers

## Usage

```python
from service_toolkit.prometheus import build_prometheus_instrumentation

PrometheusMiddleware, metrics_endpoint = build_prometheus_instrumentation(
    service_name="auth_service",
    route="/metrics",
)

app = Litestar(
    route_handlers=[metrics_endpoint, ...],
    middleware=[DefineMiddleware(PrometheusMiddleware), ...],
)
```

```python
from litestar.middleware.base import DefineMiddleware
from service_toolkit.logging import RequestContextLoggingMiddleware

app = Litestar(
    middleware=[DefineMiddleware(RequestContextLoggingMiddleware), ...],
)
```

```python
from service_toolkit.db import install_slow_query_logging

install_slow_query_logging(
    service_name="server-service",
    threshold_seconds=0.25,
)
```

```python
from litestar.middleware.base import DefineMiddleware
from service_toolkit.tracing import setup_tracing

OpenTelemetryMiddleware = setup_tracing(service_name="server-service")

middleware = []
if OpenTelemetryMiddleware is not None:
    middleware.append(DefineMiddleware(OpenTelemetryMiddleware))
```

`setup_tracing()` reads environment configuration:
- `OTEL_ENABLED` (bool, default `false`)
- `OTEL_EXPORTER_OTLP_ENDPOINT` (default `http://tempo:4317`)
- `OTEL_EXPORTER_OTLP_HEADERS` (`k=v,k2=v2`)
- `OTEL_EXPORTER_OTLP_INSECURE` (bool)
- `OTEL_TRACES_SAMPLER_ARG` (`0.0..1.0`, default `1.0`)
- `OTEL_RESOURCE_ATTRIBUTES` (`k=v,k2=v2`)
- `OTEL_INSTRUMENT_HTTPX`, `OTEL_INSTRUMENT_SQLALCHEMY`, `OTEL_INSTRUMENT_REDIS` (bool, default `true`)

```python
from service_toolkit.nats import NATSClient, NATSSettings

settings = NATSSettings.from_env()


async def publish_user_created(event: dict[str, object]) -> None:
    async with NATSClient(settings) as client:
        await client.publish_json("auth.user.created", event)
```

```python
from service_toolkit.rate_limit import rate_limited_request


@rate_limited_request(bucket="auth:login", limit=20, window_seconds=60)
async def login(request, payload):
    ...
```

If `request.app.stores["main"]` is configured, counters are stored there (Redis-backed in
most services). Otherwise, the helper falls back to a process-local window counter.

```python
from service_toolkit import AsyncLookupCache

dns_cache = AsyncLookupCache[str, list[str]](
    success_ttl_seconds=30.0,
    empty_ttl_seconds=10.0,
    is_empty=lambda values: not values,
    max_entries=2048,
    max_concurrency=64,
)
```

`NATSSettings.from_env()` automatically uses [`env-settings`](https://github.com/THEROER/env-settings)
when available, yet remains compatible with plain environment variables or manual
parameter construction.

```python
from service_toolkit.redis import Keyspace, RedisCache, RedisClient, RedisSettings

settings = RedisSettings.from_env(prefix="REDIS_")


async def get_server_summary(server_id: int) -> dict[str, object]:
    return {"server_id": server_id}


async def load(server_id: int) -> dict[str, object]:
    async with RedisClient(settings) as redis:
        cache = RedisCache(
            redis.client,
            keyspace=Keyspace("cache"),
            ttl_jitter_ratio=0.1,
        )
        return await cache.get_or_set_json(
            f"server:{server_id}",
            lambda: get_server_summary(server_id),
            ttl_seconds=60,
        )
```

Install directly from the mono-repo path:

```bash
poetry add "git+https://github.com/THEROER/service-toolkit"
```

Install with extras when needed:

```bash
poetry add "git+https://github.com/THEROER/service-toolkit[nats]"
poetry add "git+https://github.com/THEROER/service-toolkit[redis]"
```

Run tests with:

```bash
uv run pytest
```
