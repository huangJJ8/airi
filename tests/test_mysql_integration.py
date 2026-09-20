"""Only a dedicated, initially empty airi_integration_* database may be migrated."""

import os
from datetime import datetime

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from airi.environments.persistence import EnvironmentValidationRow
from airi.execution.persistence import ExecutionRunRow

pytestmark = pytest.mark.mysql_integration


def test_mysql_migrations_json_constraints():
    value = os.getenv("AIRI_MYSQL_INTEGRATION_URL")
    if not value:
        pytest.skip("NOT VERIFIED: no dedicated MySQL integration database configured")
    url = make_url(value)
    if (
        url.drivername != "mysql+pymysql"
        or not (url.database or "").startswith("airi_integration_")
        or os.getenv("AIRI_MYSQL_INTEGRATION_ALLOW_RESET") != "true"
    ):
        pytest.skip(
            "Requires dedicated airi_integration_* database and explicit reset authorization"
        )
    engine = create_engine(url, hide_parameters=True)
    try:
        if inspect(engine).get_table_names():
            pytest.skip("Integration database must be empty; existing data will not be modified")
        config = Config("alembic.ini")
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        try:
            assert inspect(engine).get_foreign_keys("execution_runs")
            assert inspect(engine).get_indexes("development_artifacts")
            with Session(engine) as session:
                now = datetime.now()
                session.add(
                    EnvironmentValidationRow(
                        validation_run_id="test",
                        environment="spark_test",
                        profile_version="1.0.0",
                        status="not_verified",
                        engine_version=None,
                        session_timezone=None,
                        started_at=now,
                        finished_at=now,
                        created_at=now,
                        report_json={"status": "not_verified", "unicode": "验收"},
                    )
                )
                session.commit()
                assert (
                    session.get(EnvironmentValidationRow, "test").report_json["unicode"] == "验收"
                )
                session.add(
                    ExecutionRunRow(
                        execution_run_id="bad-fk",
                        workflow_run_id="missing",
                        artifact_id="missing",
                        approval_id="missing",
                        execution_profile="spark_test",
                        engine="spark_sql",
                        sql_hash="0" * 64,
                        status="running",
                        started_at=now,
                        created_at=now,
                        metadata_json={},
                    )
                )
                with pytest.raises(IntegrityError):
                    session.commit()
                session.rollback()
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.check(config)
        finally:
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.downgrade(config, "base")
        assert set(inspect(engine).get_table_names()) == {"alembic_version"}
    finally:
        engine.dispose()
