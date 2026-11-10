from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from conftest import ROOT
from payments.core.settings import Settings


async def test_migrations_bring_database_to_head(migrated_settings: Settings) -> None:
    config = Config(str(ROOT / "alembic.ini"))
    head = ScriptDirectory.from_config(config).get_current_head()

    engine = create_async_engine(migrated_settings.database_url)
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT version_num FROM alembic_version"))
        version = result.scalar_one()
    await engine.dispose()

    assert version == head
