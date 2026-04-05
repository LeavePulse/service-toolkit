from __future__ import annotations

import importlib
import sys
from collections.abc import Iterator
from contextlib import contextmanager


@contextmanager
def _reset_modules(*module_names: str) -> Iterator[None]:
    previous = {module_name: sys.modules.get(module_name) for module_name in module_names}
    try:
        for module_name in module_names:
            sys.modules.pop(module_name, None)
        yield
    finally:
        for module_name in module_names:
            sys.modules.pop(module_name, None)
        for module_name, module in previous.items():
            if module is not None:
                sys.modules[module_name] = module


def test_root_package_does_not_eagerly_import_optional_modules() -> None:
    with _reset_modules(
        "service_toolkit",
        "service_toolkit.db",
        "service_toolkit.db.litestar",
        "service_toolkit.errors",
        "service_toolkit.errors.awesome_errors",
        "service_toolkit.observability",
        "service_toolkit.observability.tracing",
        "service_toolkit.state",
        "service_toolkit.state.redis",
        "service_toolkit.web",
        "service_toolkit.web.app_factory",
    ):
        import service_toolkit

        importlib.reload(service_toolkit)

        assert "service_toolkit.db.litestar" not in sys.modules
        assert "service_toolkit.errors.awesome_errors" not in sys.modules
        assert "service_toolkit.observability.tracing" not in sys.modules
        assert "service_toolkit.state.redis" not in sys.modules
        assert "service_toolkit.web.app_factory" not in sys.modules
        assert callable(service_toolkit.build_event)
        assert service_toolkit.HealthController is not None
        assert "service_toolkit.web.app_factory" not in sys.modules


def test_db_package_does_not_eagerly_import_sqlalchemy_helpers() -> None:
    with _reset_modules(
        "service_toolkit.db",
        "service_toolkit.db.litestar",
        "service_toolkit.db.observability",
        "service_toolkit.db.sqlalchemy",
    ):
        import service_toolkit.db as db

        importlib.reload(db)

        assert "service_toolkit.db.litestar" not in sys.modules
        assert "service_toolkit.db.observability" not in sys.modules
        assert "service_toolkit.db.sqlalchemy" not in sys.modules
        assert "DBConfig" in dir(db)


def test_state_package_does_not_eagerly_import_redis_helpers() -> None:
    with _reset_modules(
        "service_toolkit.state",
        "service_toolkit.state.async_singleton",
        "service_toolkit.state.cache",
        "service_toolkit.state.redis",
        "service_toolkit.state.snapshot_store",
    ):
        import service_toolkit.state as state

        importlib.reload(state)

        assert "service_toolkit.state.async_singleton" not in sys.modules
        assert "service_toolkit.state.cache" not in sys.modules
        assert "service_toolkit.state.redis" not in sys.modules
        assert "service_toolkit.state.snapshot_store" not in sys.modules
        assert "LookupCache" in dir(state)


def test_errors_and_observability_packages_stay_lazy() -> None:
    with _reset_modules(
        "service_toolkit.errors",
        "service_toolkit.errors.awesome_errors",
        "service_toolkit.observability",
        "service_toolkit.observability.logging",
        "service_toolkit.observability.metrics",
        "service_toolkit.observability.prometheus",
        "service_toolkit.observability.tracing",
    ):
        import service_toolkit.errors as errors
        import service_toolkit.observability as observability

        importlib.reload(errors)
        importlib.reload(observability)

        assert "service_toolkit.errors.awesome_errors" not in sys.modules
        assert "service_toolkit.observability.logging" not in sys.modules
        assert "service_toolkit.observability.metrics" not in sys.modules
        assert "service_toolkit.observability.prometheus" not in sys.modules
        assert "service_toolkit.observability.tracing" not in sys.modules
        assert "ErrorTranslator" in dir(errors)
        assert "setup_tracing" in dir(observability)
