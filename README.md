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
- Redis-backed request rate limiting helpers with explicit failure policies (`enforce_request_rate_limit`, `rate_limited_request`).
- Unified local/Redis/hybrid lookup cache with TTL, in-flight deduplication, and Redis fallback policies (`LookupCache`).
- gRPC client/server helpers: shared channels, default timeouts, metrics/JWT/internal-token interceptors, error translation, and proto optional-field utilities.
- (Extensible) space for other shared service utilities.

`service-toolkit` no longer owns provider contracts or provider-specific DX.
For auth-service settings models, JWT/JWKS verification, Litestar auth wiring,
public profile client helpers, and platform RBAC maps, use the provider-owned
`auth-service-sdk` package. For project RBAC maps and scope constants, use the
provider-owned `server-service-sdk` package.

> **Note**
> Install extras as needed:
> - `pip install service-toolkit[nats]` for NATS helpers
> - `pip install service-toolkit[redis]` for Redis helpers
> - `pip install service-toolkit[grpc]` for gRPC runtime helpers
> - `pip install service-toolkit[grpc-codegen]` for `lp-generate-grpc`
> - `pip install service-toolkit[tracing]` for OpenTelemetry helpers

## Usage

```python
from service_toolkit.observability.prometheus import build_prometheus_instrumentation

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
from service_toolkit.observability.logging import RequestContextLoggingMiddleware

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
from service_toolkit.observability.tracing import setup_tracing

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
from service_toolkit.messaging.nats import NATSClient, NATSSettings

settings = NATSSettings.from_env()


async def publish_user_created(event: dict[str, object]) -> None:
    async with NATSClient(settings) as client:
        await client.publish_json("auth.user.created", event)
```

```python
from service_toolkit.web.rate_limit import rate_limited_request


@rate_limited_request(bucket="auth:login", limit=20, window_seconds=60)
async def login(request, payload):
    ...
```

```python
from auth_service_sdk import build_auth_service_integration
from service_toolkit.web.app_factory import create_service_app

auth_integration = build_auth_service_integration(auth_settings=settings.auth)

app = create_service_app(
    service_name=settings.service_name,
    openapi_title="Example Service API",
    route_handlers=[ExampleController],
    auth_integration=auth_integration,
)
```

```python
from service_toolkit.grpc import build_grpc_client

client = build_grpc_client(
    key="example.auth",
    target=settings.auth.grpc_target,
    token=settings.internal.token,
    timeout_seconds=settings.auth.timeout_seconds,
)
users = client.stub(users_pb2_grpc.UsersServiceStub)

resp = await client.call(
    users.GetContact,
    users_pb2.GetContactRequest(user_id=user_id),
    resource="user",
    resource_id=user_id,
)
```

If `request.app.stores["main"]` is a Litestar `RedisStore`, counters are stored there using
an atomic Redis fixed window. When Redis is unavailable, the helper follows
`RateLimitFailureMode`: local fallback, bypass, or raise.

```python
from service_toolkit import CacheMode, LookupCache, RedisFailureMode
from service_toolkit.state.redis import Keyspace

dns_cache = LookupCache[str, list[str]](
    mode=CacheMode.HYBRID,
    local_ttl_seconds=30.0,
    empty_ttl_seconds=10.0,
    is_empty=lambda values: not values,
    max_entries=2048,
    max_concurrency=64,
    redis_client=redis.client,
    redis_keyspace=Keyspace("dns"),
    redis_ttl_seconds=30,
    redis_empty_ttl_seconds=10,
    redis_failure_mode=RedisFailureMode.LOCAL_FALLBACK,
)
```

`NATSSettings.from_env()` automatically uses [`env-settings`](https://github.com/THEROER/env-settings)
when available, yet remains compatible with plain environment variables or manual
parameter construction.

```python
from service_toolkit import CacheMode, LookupCache, RedisFailureMode
from service_toolkit.state.redis import Keyspace, RedisClient, RedisSettings

settings = RedisSettings.from_env(prefix="REDIS_")


async def get_server_summary(server_id: int) -> dict[str, object]:
    return {"server_id": server_id}


async def load(server_id: int) -> dict[str, object]:
    async with RedisClient(settings) as redis:
        cache = LookupCache[str, dict[str, object]](
            mode=CacheMode.HYBRID,
            local_ttl_seconds=60.0,
            redis_client=redis.client,
            redis_keyspace=Keyspace("cache"),
            redis_ttl_seconds=60,
            redis_ttl_jitter_ratio=0.1,
            redis_failure_mode=RedisFailureMode.LOCAL_FALLBACK,
        )
        return await cache.get(
            f"server:{server_id}",
            lambda: get_server_summary(server_id),
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
poetry add "git+https://github.com/THEROER/service-toolkit[grpc]"
poetry add "git+https://github.com/THEROER/service-toolkit[grpc-codegen]"
```

Run tests with:

```bash
uv run pytest
```
