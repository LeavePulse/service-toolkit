from service_toolkit.db.litestar import build_db_config


def test_litestar_db_factory_is_provided_by_sqlalchemy_extra() -> None:
    config = build_db_config(connection_string="sqlite+aiosqlite:///:memory:")

    assert config.sqlalchemy_config.connection_string == "sqlite+aiosqlite:///:memory:"
