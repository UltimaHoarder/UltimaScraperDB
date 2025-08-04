import mimetypes
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Generic, List, Literal, Optional, TypeVar

import aiofiles
import ultima_scraper_api
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    Security,
)
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from fastapi.routing import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import Case, UnaryExpression, nullslast, or_, orm, select
from sqlalchemy.orm.attributes import InstrumentedAttribute
from sqlalchemy.sql import case, func, or_, select
from typing_extensions import Literal
from ultima_scraper_api.apis.auth_streamliner import datetime
from ultima_scraper_collection.config import site_config_types
from ultima_scraper_collection.managers.filesystem_manager import FilesystemManager

from ultima_scraper_db.databases.ultima_archive.api.client import (
    UAClient,
    get_ua_client,
)
from ultima_scraper_db.databases.ultima_archive.api.routers.users import (
    AdvancedOptions,
    restricted,
)
from ultima_scraper_db.databases.ultima_archive.schemas.templates.site import (
    MediaModel,
    UserInfoModel,
)
from ultima_scraper_db.databases.ultima_archive.site_api import (
    MessageModel,
    PostModel,
    UserAliasModel,
    UserModel,
)
from ultima_scraper_db.helpers import extract_identifier_from_url

router = APIRouter(
    prefix="/files",
    tags=["files"],
    responses={404: {"description": "Not found"}},
)


async def iterfile(
    file_path: Path, start: int, size: int, chunk_size: int = 1024 * 1024
):
    async with aiofiles.open(file_path, "rb") as f:
        await f.seek(start)
        bytes_left = size
        while bytes_left > 0:
            read_size = min(bytes_left, chunk_size)
            chunk = await f.read(read_size)
            if not chunk:
                break
            yield chunk
            bytes_left -= len(chunk)


@router.api_route("/", methods=["GET", "HEAD"])
async def get_file(
    request: Request,
    site_name: str,
    media_id: int,
    ua_client: UAClient = Depends(get_ua_client),
):
    redis = ua_client.redis
    cache_key = f"filepath:{site_name}:{media_id}"
    cached_path_bytes = await redis.get(cache_key)

    file_path = None
    if cached_path_bytes:
        cached_path = Path(cached_path_bytes.decode())
        if cached_path.exists():
            file_path = cached_path
            # Extend cache validity
            await redis.expire(cache_key, 86400 * 7)  # 24 hours * 7 days
        else:
            # Stale cache, remove it
            await redis.delete(cache_key)

    if not file_path:
        database_api = ua_client.database_api
        datascraper = ua_client.datascraper_manager.select_datascraper(site_name)
        assert datascraper

        fsm = datascraper.filesystem_manager

        async with database_api.create_site_api(site_name) as site_db_api:
            stmt = (
                select(MediaModel)
                .where(MediaModel.id == media_id)
                .options(
                    orm.selectinload(MediaModel.user).selectinload(UserModel.aliases)
                )
            )
            result = await site_db_api.get_session().scalar(stmt)

            if not result:
                raise HTTPException(status_code=404, detail="Media not found")

            username = result.user.username
            aliases = [x.username for x in result.user.aliases]
            usernames = [username] + aliases

            performer_directory = await fsm.performer_directory(
                datascraper.api.site_name, usernames
            )
            if not performer_directory:
                raise HTTPException(
                    status_code=404, detail="Performer directory not found"
                )
            if not result.url:
                raise HTTPException(status_code=404, detail="Media URL not found")
            filename = result.url.split("/")[-1].split("?")[0]

            # NOTE: This recursive glob is very slow and the main cause of timeouts.
            # For a long-term fix, consider storing the file path directly in the database.
            found_files: list[Path] = [
                f for f in performer_directory.rglob("*") if filename in f.name
            ]

            if not found_files:
                raise HTTPException(status_code=404, detail="File not found")

            file_path = found_files[0]
            await redis.set(cache_key, str(file_path.resolve()), ex=86400)

    file_size = file_path.stat().st_size
    mime_type, _ = mimetypes.guess_type(file_path.name)
    mime_type = mime_type or "application/octet-stream"
    # Common headers for both GET and HEAD
    headers = {
        "Content-Disposition": f'inline; filename="{file_path.name}"',
        "Accept-Ranges": "bytes",
        "Content-Length": str(file_size),
    }

    # If it's a HEAD request, return headers immediately without a body.
    if request.method == "HEAD":
        return Response(
            content=None,
            status_code=200,
            headers=headers,
            media_type=mime_type,
        )

    # --- Logic for GET requests below ---

    range_header = request.headers.get("Range")
    if range_header:
        range_match = re.match(r"bytes=(\d+)-(\d*)", range_header)
        if range_match:
            start_byte = int(range_match.group(1))
            end_byte_str = range_match.group(2)
            end_byte = int(end_byte_str) if end_byte_str else file_size - 1

            if start_byte >= file_size:
                raise HTTPException(
                    status_code=416, detail="Request range not satisfiable"
                )
            end_byte = min(end_byte, file_size - 1)
            content_length = end_byte - start_byte + 1

            headers["Content-Length"] = str(content_length)
            headers["Content-Range"] = f"bytes {start_byte}-{end_byte}/{file_size}"

            return StreamingResponse(
                iterfile(file_path, start=start_byte, size=content_length),
                status_code=206,
                headers=headers,
                media_type=mime_type,
            )

    # Full file response for GET without range header
    return StreamingResponse(
        iterfile(file_path, start=0, size=file_size),
        headers=headers,
        media_type=mime_type,
    )


@router.get("/subtitles/{filename}")
async def get_subtitle(
    request: Request, filename: str, ua_client: UAClient = Depends(get_ua_client)
):
    ua_client.config.settings.subtitles_directory
    subs_dir = ua_client.config.settings.subtitles_directory
    assert subs_dir is not None, "Subtitles directory is not set in config"
    file_path = subs_dir / filename
    file_path = file_path.with_suffix(".srt")
    filename = file_path.name

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Subtitle file not found")

    return StreamingResponse(
        iterfile(file_path, start=0, size=file_path.stat().st_size),
        media_type="application/x-subrip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
