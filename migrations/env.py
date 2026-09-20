from alembic import context
from sqlalchemy import create_engine, pool

from airi.approvals import persistence  # noqa: F401
from airi.experiments import persistence as experiments_persistence  # noqa: F401
from airi.environments import persistence as environment_persistence  # noqa: F401
from airi.execution import persistence as execution_persistence  # noqa: F401
from airi.testing import persistence as testing_persistence  # noqa: F401
from airi.core.config import Settings
from airi.reflection import persistence as reflection_persistence  # noqa: F401
from airi.refinement import persistence as refinement_persistence  # noqa: F401
from airi.production import persistence as production_persistence  # noqa: F401
from airi.registry import persistence as registry_persistence  # noqa: F401
from airi.temporal import persistence as temporal_persistence  # noqa: F401
from airi.infrastructure.database import Base

config = context.config
target_metadata = Base.metadata


def migrate(connection):
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(
        url=Settings().database_url.get_secret_value(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
elif config.attributes.get("connection") is not None:
    migrate(config.attributes["connection"])
else:
    engine = create_engine(Settings().database_url.get_secret_value(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        migrate(connection)
    engine.dispose()
