import json
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from airi.core.config import Settings
from airi.main import create_app


def pytest_collection_modifyitems(config, items):
    selected = config.option.markexpr or ""
    for item in items:
        integration = next(
            (
                m
                for m in (
                    "spark_integration",
                    "mysql_integration",
                    "production_integration",
                    "identity_integration",
                    "telemetry_integration",
                )
                if item.get_closest_marker(m)
            ),
            None,
        )
        if integration and integration not in selected:
            item.add_marker(
                pytest.mark.skip(reason="Explicit integration marker selection required")
            )
        elif not integration:
            item.add_marker(pytest.mark.unit)


@pytest.fixture
def metric_payload():
    path = Path(__file__).resolve().parents[1] / "examples" / "metric_ir.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def settings(tmp_path):
    config = Settings(
        _env_file=None,
        environment="test",
        database_url=f"sqlite+pysqlite:///{(tmp_path / 'airi.db').as_posix()}",
    )
    engine = create_engine(config.database_url.get_secret_value())
    with engine.begin() as connection:
        migration = Config("alembic.ini")
        migration.attributes["connection"] = connection
        command.upgrade(migration, "head")
    engine.dispose()
    return config


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client
