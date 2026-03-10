from __future__ import annotations

import importlib
import sys


def test_messaging_package_does_not_eagerly_import_nats() -> None:
    sys.modules.pop("service_toolkit.messaging", None)
    sys.modules.pop("service_toolkit.messaging.nats", None)

    import service_toolkit.messaging as messaging

    importlib.reload(messaging)

    assert "service_toolkit.messaging.nats" not in sys.modules
    assert callable(messaging.build_event)


def test_web_package_does_not_eagerly_import_app_factory() -> None:
    sys.modules.pop("service_toolkit.web", None)
    sys.modules.pop("service_toolkit.web.app_factory", None)

    import service_toolkit.web as web

    importlib.reload(web)

    assert "service_toolkit.web.app_factory" not in sys.modules
    assert web.HealthController is not None
