"""Contracts implemented by external database modules."""

from __future__ import annotations

import re
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, TypeVar

from sqlalchemy import MetaData

from .migrations import MigrationBundle

if TYPE_CHECKING:
    from .database import DatabaseHandle

ApiT_co = TypeVar("ApiT_co", covariant=True)
ApiT = TypeVar("ApiT")

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
_EXTENSION = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
_KEY = re.compile(r"^[a-z][a-z0-9]*(?:[-_.][a-z0-9]+)*$")


def _validate_identifier(value: str, kind: str) -> None:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"Invalid {kind} identifier: {value!r}")


@dataclass(frozen=True, slots=True)
class DatabaseDefinition:
    """Domain-neutral resources needed to initialize a database module."""

    key: str
    metadata: MetaData | None = None
    migrations: MigrationBundle | None = None
    required_extensions: tuple[str, ...] = ()
    optional_extensions: tuple[str, ...] = ()
    schemas: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not _KEY.fullmatch(self.key):
            raise ValueError(f"Invalid database module key: {self.key!r}")
        if len(set(self.required_extensions)) != len(self.required_extensions):
            raise ValueError("required_extensions cannot contain duplicates")
        if len(set(self.optional_extensions)) != len(self.optional_extensions):
            raise ValueError("optional_extensions cannot contain duplicates")
        overlap = set(self.required_extensions) & set(self.optional_extensions)
        if overlap:
            raise ValueError(
                "Extensions cannot be both required and optional: "
                + ", ".join(sorted(overlap))
            )
        if len(set(self.schemas)) != len(self.schemas):
            raise ValueError("schemas cannot contain duplicates")
        for extension in (*self.required_extensions, *self.optional_extensions):
            if not _EXTENSION.fullmatch(extension):
                raise ValueError(f"Invalid extension identifier: {extension!r}")
        for schema in self.schemas:
            _validate_identifier(schema, "schema")


class DatabaseModule(Protocol[ApiT_co]):
    """Structural interface implemented by independently packaged modules."""

    @property
    def definition(self) -> DatabaseDefinition: ...

    def create_api(self, database: DatabaseHandle) -> ApiT_co | Awaitable[ApiT_co]: ...


@dataclass(slots=True)
class MountedDatabase[ApiT]:
    """A running database handle paired with its module-owned API."""

    name: str
    handle: DatabaseHandle
    api: ApiT

    async def close(self) -> None:
        await self.handle.close()
