from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from ultima_scraper_db import DatabaseConfig, DatabaseDefinition, DatabaseManager


class FakeHandle:
    def __init__(self, config: DatabaseConfig, definition: DatabaseDefinition) -> None:
        self.config = config
        self.definition = definition
        self.started_with: bool | None = None
        self.closed = False

    async def start(self, *, migrate: bool = True) -> FakeHandle:
        self.started_with = migrate
        return self

    async def close(self) -> None:
        self.closed = True


@dataclass
class FakeModule:
    definition: DatabaseDefinition
    fail_api: bool = False

    def create_api(self, database: Any) -> dict[str, Any]:
        if self.fail_api:
            raise RuntimeError("API failed")
        return {"handle": database, "domain": self.definition.key}


@pytest.fixture
def config() -> DatabaseConfig:
    return DatabaseConfig(url="postgresql+asyncpg://localhost/test")


@pytest.mark.asyncio
async def test_manager_runs_multiple_opaque_modules(config: DatabaseConfig) -> None:
    handles: list[FakeHandle] = []

    def factory(runtime_config: DatabaseConfig, definition: DatabaseDefinition) -> Any:
        handle = FakeHandle(runtime_config, definition)
        handles.append(handle)
        return handle

    manager = DatabaseManager(handle_factory=factory)
    archive = await manager.run(
        FakeModule(DatabaseDefinition(key="usdb-archive")),
        config,
    )
    vault = await manager.run(
        FakeModule(DatabaseDefinition(key="face-vault")),
        config,
        migrate=False,
    )

    assert set(manager.mounted) == {"usdb-archive", "face-vault"}
    assert archive.api["domain"] == "usdb-archive"
    assert vault.api["domain"] == "face-vault"
    assert handles[0].started_with is True
    assert handles[1].started_with is False

    await manager.close()
    assert all(handle.closed for handle in handles)
    assert not manager.mounted


@pytest.mark.asyncio
async def test_manager_rejects_duplicate_instance_names(config: DatabaseConfig) -> None:
    manager = DatabaseManager(handle_factory=FakeHandle)  # type: ignore[arg-type]
    module = FakeModule(DatabaseDefinition(key="face-vault"))
    await manager.run(module, config)

    with pytest.raises(ValueError, match="already mounted"):
        await manager.run(module, config)

    await manager.close()


@pytest.mark.asyncio
async def test_manager_closes_handle_when_api_creation_fails(
    config: DatabaseConfig,
) -> None:
    handles: list[FakeHandle] = []

    def factory(runtime_config: DatabaseConfig, definition: DatabaseDefinition) -> Any:
        handle = FakeHandle(runtime_config, definition)
        handles.append(handle)
        return handle

    manager = DatabaseManager(handle_factory=factory)
    module = FakeModule(DatabaseDefinition(key="broken"), fail_api=True)

    with pytest.raises(RuntimeError, match="API failed"):
        await manager.run(module, config)

    assert handles[0].closed is True
    assert not manager.mounted
