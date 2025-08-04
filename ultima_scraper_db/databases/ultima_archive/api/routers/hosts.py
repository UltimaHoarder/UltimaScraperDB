from fastapi import APIRouter, Response
from pydantic import BaseModel

from ultima_scraper_db.databases.ultima_archive.api.client import UAClient
from ultima_scraper_db.databases.ultima_archive.schemas.management import HostModel
from ultima_scraper_db.databases.ultima_archive.api.client import (
    UAClient,
    get_ua_client,
)
from fastapi import APIRouter, Depends, HTTPException, Request, Response

router = APIRouter(
    prefix="/hosts",
    tags=["hosts"],
    responses={404: {"description": "Not found"}},
)


@router.get("/")
async def get_hosts(
    request: Request,
):
    database_api = get_ua_client(request).database_api

    async with database_api.create_management_api() as management_api:
        hosts = await management_api.get_hosts()
        return hosts


class PyHost(BaseModel):
    name: str
    identifier: str
    password: str
    source: bool
    active: bool


@router.post("/create")
async def create_host(request: Request, host: PyHost):
    database_api = get_ua_client(request).database_api

    async with database_api.create_management_api() as management_api:
        db_host = HostModel(
            name=host.name,
            identifier=host.identifier,
            password=host.password,
            source=host.source,
            active=host.active,
        )
        await management_api.create_or_update_host(db_host)
        return db_host
