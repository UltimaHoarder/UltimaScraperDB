import re
from dataclasses import asdict, dataclass
from typing import Any, Generic, List, Literal, Optional, TypeVar

import ultima_scraper_api
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Security
from fastapi.encoders import jsonable_encoder
from fastapi.routing import APIRouter
from inflection import singularize, underscore
from pydantic import BaseModel, Field
from sqlalchemy import Case, UnaryExpression, nullslast, or_, orm, select
from sqlalchemy.orm.attributes import InstrumentedAttribute
from sqlalchemy.sql import case, func, or_, select
from typing_extensions import Literal
from ultima_scraper_api.apis.auth_streamliner import datetime

from ultima_scraper_db.databases.ultima_archive.api.client import (
    UAClient,
    get_ua_client,
)
from ultima_scraper_db.databases.ultima_archive.api.routers.users import (
    AdvancedOptions,
    restricted,
)
from ultima_scraper_db.databases.ultima_archive.schemas.templates.site import (
    ContentMediaAssoModel,
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
    prefix="/media",
    tags=["media"],
    responses={404: {"description": "Not found"}},
)


@router.get("/")
async def get_media(
    request: Request,
    site_name: str,
    media_id: int,
    ua_client: UAClient = Depends(get_ua_client),
):
    _database_api = ua_client.database_api


class MediaIDs(BaseModel):
    media_ids: List[int]


@router.post("/{site_name}/bulk")
async def get_bulk_media(
    site_name: str,
    item: MediaIDs,
    ua_client: UAClient = Depends(get_ua_client),
):
    database_api = ua_client.database_api
    async with database_api.create_site_api(site_name) as site_api:
        stmt = select(MediaModel).where(MediaModel.id.in_(item.media_ids))
        result = await site_api.get_session().scalars(stmt)
        media_list = result.unique().all()
        return media_list


class ContentIDs(BaseModel):
    content_ids: List[int]


@router.post("/{site_name}/{content_type}/bulk")
async def get_bulk_media_by_content_id(
    site_name: str,
    content_type: str,
    item: ContentIDs,
    ua_client: UAClient = Depends(get_ua_client),
):
    database_api = ua_client.database_api
    async with database_api.create_site_api(site_name) as site_api:
        content_field = f"{singularize(content_type)}_id"
        stmt = (
            select(MediaModel)
            .join(
                ContentMediaAssoModel, ContentMediaAssoModel.media_id == MediaModel.id
            )
            .where(getattr(ContentMediaAssoModel, content_field).in_(item.content_ids))
        )
        result = await site_api.get_session().scalars(stmt)
        media_list = result.unique().all()
        return media_list
