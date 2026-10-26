from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import URL

from universal_video_maker.config import get_settings


def test_initial_migration_upgrades_and_downgrades_sqlite(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "migration.db"
    database_url = str(URL.create("sqlite", database=str(database_path)))
    monkeypatch.setenv("UVM_DATABASE_URL", database_url)
    get_settings.cache_clear()
    config = Config("alembic.ini")
    try:
        command.upgrade(config, "head")
        command.check(config)
        engine = create_engine(database_url)
        try:
            table_names = set(inspect(engine).get_table_names())
            assert {"topics", "claims", "episodes", "audit_events"} <= table_names

            command.downgrade(config, "base")
            table_names = set(inspect(engine).get_table_names())
            assert "topics" not in table_names
            assert "audit_events" not in table_names
        finally:
            engine.dispose()
    finally:
        get_settings.cache_clear()
