"""Typed runtime configuration for a database instance."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.engine import URL, make_url


@dataclass(frozen=True, slots=True)
class DatabaseConfig:
    """Connection and pool settings for one mounted database instance."""

    url: str | URL
    echo: bool = False
    pool_pre_ping: bool = True
    pool_size: int | None = 5
    max_overflow: int | None = 10
    pool_timeout: float | None = 30.0
    pool_recycle: int | None = 1800
    connect_args: Mapping[str, Any] = field(default_factory=dict)
    execution_options: Mapping[str, Any] = field(default_factory=dict)
    engine_options: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        parsed = make_url(self.url)
        if not parsed.drivername:
            raise ValueError("Database URL must include a SQLAlchemy driver name")
        if self.pool_size is not None and self.pool_size < 1:
            raise ValueError("pool_size must be at least 1")
        if self.max_overflow is not None and self.max_overflow < 0:
            raise ValueError("max_overflow cannot be negative")
        if self.pool_timeout is not None and self.pool_timeout <= 0:
            raise ValueError("pool_timeout must be positive")
        if self.pool_recycle is not None and self.pool_recycle < -1:
            raise ValueError("pool_recycle must be -1 or greater")

    @property
    def sqlalchemy_url(self) -> URL:
        """Return the parsed SQLAlchemy URL."""

        return make_url(self.url)

    @property
    def redacted_url(self) -> str:
        """Return a safe URL suitable for logs."""

        return self.sqlalchemy_url.render_as_string(hide_password=True)

    def create_engine_options(self) -> dict[str, Any]:
        """Build keyword arguments for SQLAlchemy's async engine factory."""

        options: dict[str, Any] = {
            "echo": self.echo,
            "pool_pre_ping": self.pool_pre_ping,
            "connect_args": dict(self.connect_args),
            "execution_options": dict(self.execution_options),
        }
        optional_pool_values = {
            "pool_size": self.pool_size,
            "max_overflow": self.max_overflow,
            "pool_timeout": self.pool_timeout,
            "pool_recycle": self.pool_recycle,
        }
        options.update(
            {
                key: value
                for key, value in optional_pool_values.items()
                if value is not None
            }
        )
        options.update(dict(self.engine_options))
        return options
