from fastapi import APIRouter, Request

from ultima_scraper_db.databases.ultima_archive.api.client import (
    get_ua_client,
)

router = APIRouter(
    prefix="/client",
    tags=["client"],
    responses={404: {"description": "Not found"}},
)


@router.get("/whoami/{identifier}")
async def whoami(request: Request, identifier: int | str):
    database_api = get_ua_client(request).database_api
    async with database_api.create_management_api() as management_api:
        if isinstance(identifier, int):
            server = await management_api.get_server(
                server_id=identifier, server_name=None
            )
        else:
            if identifier.isdigit():
                server = await management_api.get_server(
                    server_id=int(identifier), server_name=None
                )
            else:
                server = await management_api.get_server(
                    server_id=None, server_name=identifier
                )
    return server


@router.post("/")
async def get_ip(request: Request):
    assert request.client
    client_host = request.client.host
    return {"client_ip": client_host}
