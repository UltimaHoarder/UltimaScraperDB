# UltimaScraperDB

UltimaScraperDB (USDB) is the domain-neutral database runtime used to mount
independent Ultima database modules. It owns PostgreSQL engine and session
lifecycle, extension/schema preparation, and Alembic migration execution.

USDB deliberately contains no scraper, archive, media, face, or site models.
Those belong to separately installable modules such as UltimaScraperDBArchive
and UltimaSightVault.

```python
manager = DatabaseManager()

archive = await manager.run(
    module=USDBArchiveModule(),
    config=DatabaseConfig(url=archive_url),
)

vault = await manager.run(
    module=UltimaSightVaultModule(),
    config=DatabaseConfig(url=vault_url),
)
```

Each module supplies a `DatabaseDefinition` and constructs its own domain API
from the generic `DatabaseHandle` returned by USDB.

The previous implementation is available in Git history.
