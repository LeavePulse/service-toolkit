# Service Toolkit

Reusable infrastructure helpers for LeavePulse services. Currently provides:

- Prometheus instrumentation factory (`build_prometheus_instrumentation`) that exposes a middleware and metrics endpoint tailored per service.
- Standard logging configuration builder (`build_standard_logging_config`) with health/metrics suppression support (defaults can be overridden).
- Simple health-check controller for Litestar applications (`HealthController`).
- Snowflake ID generation helpers (configurable epoch/node setup).
- A lightweight async NATS client wrapper (`NATSClient`) with convenience configuration.
- (Extensible) space for other shared service utilities.

> **Note**
> Install the `nats` extra (`pip install service-toolkit[nats]`) to use the NATS helpers.

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


```python
from service_toolkit.nats import NATSClient, NATSSettings

settings = NATSSettings.from_env()


async def publish_user_created(event: dict[str, object]) -> None:
    async with NATSClient(settings) as client:
        await client.publish_json("auth.user.created", event)
```

`NATSSettings.from_env()` automatically uses [`env-settings`](https://github.com/THEROER/env-settings)
when available, yet remains compatible with plain environment variables or manual
parameter construction.

Install directly from the mono-repo path:

```bash
poetry add "git+https://github.com/THEROER/service-toolkit"
```

Install with the NATS extra when the messaging helpers are needed:

```bash
poetry add "git+https://github.com/THEROER/service-toolkit[nats]"
```

Run tests with:

```bash
poetry install
poetry run pytest
```
