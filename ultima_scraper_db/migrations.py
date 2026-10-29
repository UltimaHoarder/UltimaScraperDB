"""Alembic migration definitions and execution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

if TYPE_CHECKING:
    from alembic.config import Config


@dataclass(frozen=True, slots=True)
class MigrationBundle:
    """Filesystem-backed Alembic configuration owned by a database module."""

    root: Path
    config_filename: str = "alembic.ini"
    script_directory: str = "alembic"
    version_table: str = "alembic_version"

    def __post_init__(self) -> None:
        root = Path(self.root).resolve()
        object.__setattr__(self, "root", root)
        if not self.config_path.is_file():
            raise ValueError(f"Alembic config does not exist: {self.config_path}")
        if not self.script_path.is_dir():
            raise ValueError(
                f"Alembic script directory does not exist: {self.script_path}"
            )

    @property
    def config_path(self) -> Path:
        return self.root / self.config_filename

    @property
    def script_path(self) -> Path:
        return self.root / self.script_directory

    def create_config(self, database_url: str) -> Config:
        """Create an isolated Alembic config for one runtime invocation."""

        from alembic.config import Config

        config = Config(self.config_path.as_posix())
        config.set_main_option("script_location", self.script_path.as_posix())
        # ConfigParser treats percent signs in escaped credentials as interpolation.
        config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
        config.attributes["usdb_version_table"] = self.version_table
        return config


class MigrationRunner:
    """Run module-owned Alembic migrations on an existing async engine."""

    async def upgrade(
        self,
        engine: AsyncEngine,
        bundle: MigrationBundle,
        revision: str = "head",
    ) -> None:
        from alembic import command

        database_url = engine.url.render_as_string(hide_password=False)
        config = bundle.create_config(database_url)

        def run_upgrade(connection: Connection) -> None:
            config.attributes["connection"] = connection
            command.upgrade(config, revision)

        async with engine.begin() as connection:
            await connection.run_sync(run_upgrade)
