from typing import TYPE_CHECKING

import redis.asyncio as _redis
from fastapi import Request

import ultima_scraper_db
from ultima_scraper_db import ALEMBICA_PATH
from ultima_scraper_db.databases.rest_api import RestAPI
from ultima_scraper_db.databases.ultima_archive import merged_metadata
from ultima_scraper_db.managers.database_manager import Alembica, DatabaseManager


def get_ua_client(request: Request) -> "UAClient":
    return request.app


class UAClient(RestAPI):
    redis = _redis.from_url("redis://localhost:6379")  # type: ignore

    def __init__(
        self,
    ):

        from ultima_scraper_collection.config import UltimaScraperCollectionConfig

        from ultima_scraper_db.databases.ultima_archive.api.app import routers

        super().__init__(
            root_path="./",
            proxy_headers=True,
        )
        self.include_routers(routers)

        config = UltimaScraperCollectionConfig()
        self.config = config.load_or_create_default_config()

        self.dev = ultima_scraper_db.dev_mode

        # Register async startup hook
        self.add_event_handler("startup", self.startup)  # type: ignore

    async def startup(self):
        from ultima_scraper_collection.managers.datascraper_manager.datascraper_manager import (
            DataScraperManager,
        )

        from ultima_scraper_db.databases.ultima_archive.database_api import ArchiveAPI
        from ultima_scraper_db.managers.database_manager import (
            Alembica,
            DatabaseManager,
        )

        db_config = self.config.settings.databases[0].connection_info.model_dump()
        db_manager = DatabaseManager()
        database = db_manager.create_database(
            **db_config, alembica=Alembica(ALEMBICA_PATH), metadata=merged_metadata
        )
        await database.init_db()
        self.database_api = ArchiveAPI(database)

        self.datascraper_manager = DataScraperManager(
            self.database_api.server_manager, self.config
        )

    def select_site_api(self, site_name: str):
        datascraper = self.datascraper_manager.find_datascraper(site_name)
        assert datascraper is not None
        return datascraper.api
