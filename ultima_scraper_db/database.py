"""Generic async database handle and lifecycle."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from enum import StrEnum
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.schema import CreateSchema

from .config import DatabaseConfig
from .definitions import DatabaseDefinition
from .migrations import MigrationRunner

EngineFactory = Callable[..., AsyncEngine]
logger = logging.getLogger(__name__)


class DatabaseState(StrEnum):
    CREATED = "created"
    STARTING = "starting"
    RUNNING = "running"
    CLOSED = "closed"


class DatabaseHandle:
    """A running engine and session factory for one database definition."""

    def __init__(
        self,
        config: DatabaseConfig,
        definition: DatabaseDefinition,
        *,
        engine_factory: EngineFactory = create_async_engine,
        migration_runner: MigrationRunner | None = None,
    ) -> None:
        self.config = config
        self.definition = definition
        self.engine = engine_factory(
            config.sqlalchemy_url,
            **config.create_engine_options(),
        )
        self.sessionmaker = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        self.migration_runner = migration_runner or MigrationRunner()
        self.state = DatabaseState.CREATED

    @property
    def is_running(self) -> bool:
        return self.state is DatabaseState.RUNNING

    async def start(self, *, migrate: bool = True) -> DatabaseHandle:
        if self.state is DatabaseState.RUNNING:
            return self
        if self.state is not DatabaseState.CREATED:
            raise RuntimeError(
                f"Cannot start database while state is {self.state.value}"
            )

        self.state = DatabaseState.STARTING
        try:
            await self._prepare_database()
            if migrate and self.definition.migrations is not None:
                await self.migration_runner.upgrade(
                    self.engine,
                    self.definition.migrations,
                )
            await self.check_health()
        except BaseException:
            await self.engine.dispose()
            self.state = DatabaseState.CLOSED
            raise

        self.state = DatabaseState.RUNNING
        return self

    async def _prepare_database(self) -> None:
        async with self.engine.begin() as connection:
            preparer = connection.dialect.identifier_preparer
            for extension in self.definition.required_extensions:
                quoted_extension = preparer.quote(extension)
                await connection.execute(
                    text(f"CREATE EXTENSION IF NOT EXISTS {quoted_extension}")
                )
            for extension in self.definition.optional_extensions:
                quoted_extension = preparer.quote(extension)
                try:
                    async with connection.begin_nested():
                        await connection.execute(
                            text(f"CREATE EXTENSION IF NOT EXISTS {quoted_extension}")
                        )
                except Exception as exc:
                    logger.info(
                        "Optional extension %s is unavailable: %s",
                        extension,
                        exc,
                    )
            for schema in self.definition.schemas:
                await connection.execute(CreateSchema(schema, if_not_exists=True))

    async def check_health(self) -> None:
        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    def _require_running(self) -> None:
        if not self.is_running:
            raise RuntimeError("Database is not running")

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Yield a short-lived session and roll it back when an operation fails."""

        self._require_running()
        async with self.sessionmaker() as session:
            try:
                yield session
            except BaseException:
                await session.rollback()
                raise

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        """Yield a session inside a transaction managed by SQLAlchemy."""

        async with self.session() as session:
            async with session.begin():
                yield session

    async def close(self) -> None:
        if self.state is DatabaseState.CLOSED:
            return
        await self.engine.dispose()
        self.state = DatabaseState.CLOSED

    async def __aenter__(self) -> DatabaseHandle:
        return await self.start()

    async def __aexit__(self, *_exc: Any) -> None:
        await self.close()
