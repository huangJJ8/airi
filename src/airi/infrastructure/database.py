from collections.abc import Iterator

from fastapi import Request
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from airi.core.config import Settings


class Base(DeclarativeBase):
    """Future persistence models inherit here, separately from domain schemas."""


class Database:
    def __init__(self, settings: Settings):
        self.engine = create_engine(
            settings.database_url.get_secret_value(), pool_pre_ping=True, hide_parameters=True
        )
        self.session_factory = sessionmaker(
            bind=self.engine, autoflush=False, expire_on_commit=False
        )

    def session(self) -> Iterator[Session]:
        # Services own commit. Errors roll back, and every request closes its session.
        with self.session_factory() as session:
            try:
                yield session
            except Exception:
                session.rollback()
                raise

    def dispose(self) -> None:
        self.engine.dispose()


def get_session(request: Request) -> Iterator[Session]:
    yield from request.app.state.database.session()
