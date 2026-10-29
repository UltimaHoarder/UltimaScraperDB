from __future__ import annotations

import pytest

from ultima_scraper_db import DatabaseDefinition


def test_definition_accepts_domain_neutral_resources() -> None:
    definition = DatabaseDefinition(
        key="face-vault",
        required_extensions=("uuid-ossp", "vector"),
        schemas=("face_vault",),
    )

    assert definition.key == "face-vault"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"key": "Face Vault"},
        {"key": "face-vault", "required_extensions": ("vector; drop",)},
        {
            "key": "face-vault",
            "required_extensions": ("vector",),
            "optional_extensions": ("vector",),
        },
        {"key": "face-vault", "schemas": ("private.faces",)},
        {"key": "face-vault", "schemas": ("vault", "vault")},
    ],
)
def test_definition_rejects_unsafe_or_duplicate_identifiers(
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        DatabaseDefinition(**kwargs)  # type: ignore[arg-type]
