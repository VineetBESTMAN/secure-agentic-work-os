from pathlib import Path

from alembic import command
from alembic.config import Config

from app.core.config import get_settings

BACKEND_DIR = Path(__file__).resolve().parents[2]


def build_alembic_config(database_url: str | None = None) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    if database_url:
        config.attributes["database_url"] = database_url
    config.attributes["vector_dimensions"] = get_settings().vector_dimensions
    return config


def upgrade_database(database_url: str | None = None, revision: str = "head") -> None:
    command.upgrade(build_alembic_config(database_url), revision)


def downgrade_database(database_url: str | None = None, revision: str = "base") -> None:
    command.downgrade(build_alembic_config(database_url), revision)
