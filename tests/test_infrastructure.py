import json
import logging
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from pydantic import ValidationError
from sqlalchemy import Integer, select
from sqlalchemy.orm import Mapped, mapped_column

from airi.core.config import Settings
from airi.infrastructure.database import Base, Database
from airi.observability.logging import JsonFormatter, configure_logging


class SampleRow(Base):
    __tablename__ = "test_sample"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)


def test_settings_read_environment_and_hide_secrets(monkeypatch):
    monkeypatch.setenv("AIRI_APP_NAME", "AIRI test")
    monkeypatch.setenv("AIRI_LLM_API_KEY", "private-secret-value")
    monkeypatch.setenv("AIRI_LLM_TIMEOUT_SECONDS", "12")
    settings = Settings(_env_file=None)
    assert settings.app_name == "AIRI test"
    assert settings.llm_timeout_seconds == 12
    assert "private-secret-value" not in repr(settings)
    assert "change-me" not in settings.model_dump_json()


@pytest.mark.parametrize(
    "update",
    [
        {"database_url": "postgresql://localhost/db"},
        {"database_url": "bad-url"},
        {"database_url": "mysql+pymysql://localhost"},
        {"llm_timeout_seconds": 0},
        {"llm_base_url": "not-a-url"},
        {"log_level": "TRACE"},
        {"unknown": True},
    ],
)
def test_invalid_configuration(update):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **update)


def test_database_transactions_and_cleanup(settings):
    db = Database(settings)
    Base.metadata.create_all(db.engine)
    session_scope = contextmanager(db.session)
    try:
        with session_scope() as session:
            session.add(SampleRow(id=1))
            session.commit()
        with pytest.raises(RuntimeError):
            with session_scope() as session:
                session.add(SampleRow(id=2))
                session.flush()
                raise RuntimeError("rollback")
        with session_scope() as session:
            assert session.scalars(select(SampleRow.id)).all() == [1]
            session.add(SampleRow(id=3))
            session.flush()
        with session_scope() as session:
            assert session.scalars(select(SampleRow.id)).all() == [1]
        with patch.object(db.engine, "dispose") as dispose:
            db.dispose()
            dispose.assert_called_once()
    finally:
        db.dispose()


def test_json_logging_and_idempotent_setup():
    configure_logging("INFO")
    logger = logging.getLogger("airi")
    count = len(logger.handlers)
    configure_logging("DEBUG")
    assert len(logger.handlers) == count
    record = logging.LogRecord("airi.http", logging.INFO, "", 1, "request_completed", (), None)
    record.request_id = "test-id"
    record.status_code = 200
    parsed = json.loads(JsonFormatter().format(record))
    assert parsed["request_id"] == "test-id"
    assert parsed["status_code"] == 200
    assert parsed["level"] == "INFO"
