"""Coordinator for mounting independent database modules."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Any, TypeVar, cast

from .config import DatabaseConfig
from .database import DatabaseHandle
from .definitions import DatabaseDefinition, DatabaseModule, MountedDatabase

ApiT = TypeVar("ApiT")
HandleFactory = Callable[[DatabaseConfig, DatabaseDefinition], DatabaseHandle]


class DatabaseManager:
    """Run any module that satisfies the domain-neutral `DatabaseModule` contract."""

    def __init__(self, *, handle_factory: HandleFactory | None = None) -> None:
        self._handle_factory = handle_factory or DatabaseHandle
        self._mounted: dict[str, MountedDatabase[Any]] = {}
        self._lock = asyncio.Lock()

    @property
    def mounted(self) -> Mapping[str, MountedDatabase[Any]]:
        return MappingProxyType(self._mounted)

    async def run(
        self,
        module: DatabaseModule[ApiT],
        config: DatabaseConfig,
        *,
        instance_name: str | None = None,
        migrate: bool = True,
    ) -> MountedDatabase[ApiT]:
        """Start a module and return its generic handle plus domain-owned API."""

        name = instance_name or module.definition.key
        if not name:
            raise ValueError("instance_name cannot be empty")

        async with self._lock:
            if name in self._mounted:
                raise ValueError(f"Database instance is already mounted: {name}")

            handle = self._handle_factory(config, module.definition)
            try:
                await handle.start(migrate=migrate)
                api_or_awaitable = module.create_api(handle)
                if inspect.isawaitable(api_or_awaitable):
                    api = await api_or_awaitable
                else:
                    api = api_or_awaitable
            except BaseException:
                await handle.close()
                raise

            mounted = MountedDatabase(name=name, handle=handle, api=cast(ApiT, api))
            self._mounted[name] = mounted
            return mounted

    def get(self, instance_name: str) -> MountedDatabase[Any]:
        try:
            return self._mounted[instance_name]
        except KeyError as exc:
            raise KeyError(
                f"Database instance is not mounted: {instance_name}"
            ) from exc

    async def stop(self, instance_name: str) -> None:
        async with self._lock:
            try:
                mounted = self._mounted.pop(instance_name)
            except KeyError as exc:
                raise KeyError(
                    f"Database instance is not mounted: {instance_name}"
                ) from exc
            await mounted.close()

    async def close(self) -> None:
        async with self._lock:
            mounted = list(reversed(self._mounted.values()))
            self._mounted.clear()
            for database in mounted:
                await database.close()

    async def __aenter__(self) -> DatabaseManager:
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        await self.close()
