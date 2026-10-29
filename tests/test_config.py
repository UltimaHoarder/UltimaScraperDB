from __future__ import annotations

from typing import Any

import pytest

from ultima_scraper_db import DatabaseConfig


def test_database_config_redacts_password_and_builds_engine_options() -> None:
    config = DatabaseConfig(
        url="postgresql+asyncpg://user:secret@localhost/archive",
        pool_size=3,
        connect_args={"server_settings": {"application_name": "test"}},
    )

    assert "secret" not in config.redacted_url
    assert "***" in config.redacted_url
    options = config.create_engine_options()
    assert options["pool_size"] == 3
    assert options["connect_args"]["server_settings"]["application_name"] == "test"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"pool_size": 0}, "pool_size"),
        ({"max_overflow": -1}, "max_overflow"),
        ({"pool_timeout": 0}, "pool_timeout"),
        ({"pool_recycle": -2}, "pool_recycle"),
    ],
)
def test_database_config_rejects_invalid_pool_values(
    kwargs: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        DatabaseConfig(url="postgresql+asyncpg://localhost/test", **kwargs)
