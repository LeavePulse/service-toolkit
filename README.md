# Service Toolkit

Reusable infrastructure helpers for LeavePulse services. Currently provides:

- Prometheus instrumentation factory (`build_prometheus_instrumentation`) that exposes a middleware and metrics endpoint tailored per service.
- Standard logging configuration builder (`build_standard_logging_config`) with health/metrics suppression support (defaults can be overridden).
- (Extensible) space for other shared service utilities.

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

Install directly from the mono-repo path:

```bash
poetry add "git+https://github.com/LeavePulse/service-toolkit"
```

Run tests with:

```bash
poetry install
poetry run pytest
```
