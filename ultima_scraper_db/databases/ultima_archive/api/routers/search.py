import re
from dataclasses import asdict, dataclass
from typing import Any, Generic, List, Literal, Optional, TypeVar
from pydantic import BaseModel

import ultima_scraper_api
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Security
from fastapi.encoders import jsonable_encoder
from fastapi.routing import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import Case, UnaryExpression, exists, nullslast, or_, orm, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm.attributes import InstrumentedAttribute
from sqlalchemy.orm.strategy_options import lazyload
from sqlalchemy.sql import case, func, literal, or_, select, union_all
from sqlalchemy.sql.elements import KeyedColumnElement
from typing_extensions import Literal
from ultima_scraper_api.apis.auth_streamliner import datetime

from typing import TypeVar, Generic
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

T = TypeVar("T")
router = APIRouter(
    prefix="/search",
    tags=["search"],
    responses={404: {"description": "Not found"}},
)


class PaginatedResponse(BaseModel, Generic[T]):
    total: int
    results: List[T]


class Filters:
    def __init__(
        self,
        site_name: str,
        q: str = Query(""),
        order_by: Optional[str] = Query(None),
        order_direction: Literal["asc", "desc"] = Query("asc"),
        has_ppv: Optional[bool] = Query(None),
        prioritize_exact_match: bool = Query(False),
    ):
        self.site_name = site_name
        self.q = q
        self.order_by = order_by
        self.order_direction = order_direction
        self.has_ppv = has_ppv
        self.prioritize_exact_match = prioritize_exact_match

    def as_dict(self, exclude_none: bool = False) -> dict[str, Any]:
        data = self.__dict__
        return (
            {k: v for k, v in data.items() if v is not None} if exclude_none else data
        )


@router.get("/users/{site_name}")
async def search_users(
    filters: Filters = Depends(),
    page: int = Query(1, ge=1),
    limit: int = Query(20, le=50),
    ua_client: UAClient = Depends(get_ua_client),
):
    site_name = filters.site_name
    q = filters.q
    order_by = filters.order_by or "id"
    order_direction = filters.order_direction
    has_ppv = filters.has_ppv

    database_api = ua_client.database_api
    site_api = ua_client.select_site_api(site_name)
    # Ensure valid order direction
    if order_direction.lower() not in ["asc", "desc"]:
        raise HTTPException(
            status_code=400, detail="Invalid order_direction, must be 'asc' or 'desc'."
        )
    offset = (page - 1) * limit  # calculate offset for pagination
    q = q.replace(" ", "")
    q = q.replace("@", "")
    q = extract_identifier_from_url(q)
    async with database_api.create_site_api(site_name) as site_db_api:

        # Create subqueries for both paid posts and messages
        paid_posts_subquery = (
            select(PostModel.user_id, func.count().label("posts_ppv_count"))
            .where(PostModel.paid.is_(True))
            .group_by(PostModel.user_id)
            .subquery()
        )

        paid_messages_subquery = (
            select(MessageModel.user_id, func.count().label("messages_ppv_count"))
            .where(MessageModel.paid.is_(True))
            .group_by(MessageModel.user_id)
            .subquery()
        )
        last_post_date_subquery = (
            select(
                PostModel.user_id,
                func.max(PostModel.created_at).label("last_posted_date"),
            )
            .group_by(PostModel.user_id)
            .subquery()
        )

        stmt = (
            select(
                UserModel,
                (
                    func.coalesce(paid_posts_subquery.c.posts_ppv_count, 0)
                    + func.coalesce(paid_messages_subquery.c.messages_ppv_count, 0)
                ).label("ppv_count"),
                last_post_date_subquery.c.last_posted_date,
            )
            .outerjoin(
                paid_posts_subquery, UserModel.id == paid_posts_subquery.c.user_id
            )
            .outerjoin(
                paid_messages_subquery, UserModel.id == paid_messages_subquery.c.user_id
            )
            .outerjoin(UserAliasModel)
            .outerjoin(
                last_post_date_subquery,
                UserModel.id == last_post_date_subquery.c.user_id,
            )
            .where(
                or_(
                    UserModel.username.ilike(f"%{q}%"),
                    UserAliasModel.username.ilike(f"%{q}%"),
                )
            )
        )
        if has_ppv:
            # Only include users who have at least one paid post or paid message
            stmt = stmt.where(
                or_(
                    paid_posts_subquery.c.posts_ppv_count.isnot(None),
                    paid_messages_subquery.c.messages_ppv_count.isnot(None),
                )
            )

        # Always order by exact match first
        ordering: list[
            Case[Any]
            | UnaryExpression[datetime | int]
            | InstrumentedAttribute[datetime]
            | InstrumentedAttribute[int]
        ] = []

        if filters.prioritize_exact_match:
            ordering.append(
                case((func.lower(UserModel.username) == q.lower(), 0), else_=1)
            )

        # Then add field-based ordering if specified
        if order_by in ["downloaded_at", "size"]:
            field = (
                UserModel.downloaded_at
                if order_by == "downloaded_at"
                else UserInfoModel.size
            )
            if order_direction.lower() == "desc":
                ordering.append(nullslast(field.desc()))
            else:
                ordering.append(field)

        # Always order by id last
        ordering.append(UserModel.id)

        # Apply to query
        stmt = stmt.order_by(*ordering)

        total_stmt = select(func.count(func.distinct(stmt.subquery().c.id)))
        total = await site_db_api.get_session().scalar(total_stmt)
        assert total is not None, "Total count should not be None"
        stmt = (
            stmt.offset(offset)
            .limit(limit)
            .options(*restricted)
            .options(orm.selectinload(UserModel.user_info))
        )
        results = await site_db_api.get_session().execute(stmt)
        final_users: list[dict[str, Any]] = []
        accurate_username = False
        for user, ppv_count, last_posted_date in results.unique():
            print(
                f"Processing user: {user.username}, PPV Count: {ppv_count}, Last Post Date: {last_posted_date}"
            )
            temp_user = jsonable_encoder(user)
            if temp_user["user_info"]:
                temp_user["user_info"]["ppv_count"] = ppv_count
            temp_user["last_posted_at"] = last_posted_date
            if q.lower() == str(user.id) or q.lower() == user.username.lower():
                accurate_username = True
            final_users.append(temp_user)

        _all_are_performers = all(entry.get("performer") for entry in final_users)
        if not final_users or not accurate_username:
            async with site_api.login_context(guest=True) as authed:
                if authed:
                    site_user = await authed.get_user(q)
                    if site_user:
                        db_user = await site_db_api.get_user(
                            site_user.id,
                            extra_options=restricted,
                        )
                        user = await site_db_api.create_or_update_user(
                            site_user, db_user, performer_optimize=True
                        )
                        if site_user.username != q:
                            _user_alias = await site_db_api.create_or_update_user_alias(
                                user, q
                            )
                        user.content_manager = None
                        await user.awaitable_attrs.aliases
                        await user.awaitable_attrs.user_info
                        await user.awaitable_attrs.remote_urls
                        results = await site_db_api.get_session().scalars(stmt)
                        # fix this, we get json encode max recursion error, why? I don't know. But it could be because of the awaitable_attrs
                        # _abc = results.all()
                        final_users = [
                            jsonable_encoder(
                                user, exclude={"user_info": "user", "aliases": "user"}
                            )
                            for user in results.all()
                        ]

        return PaginatedResponse[dict[str, Any]](
            total=total, results=jsonable_encoder(final_users)
        )


@router.get(
    "/{site_name}/content",
    response_model=PaginatedResponse[dict[str, Any]],
)
async def search_content(
    category: Optional[Literal["posts", "messages"]] = None,
    filters: Filters = Depends(),
    user_id: int | None = None,
    has_media: Optional[bool] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(100, le=1000),
    ua_client: UAClient = Depends(get_ua_client),
):

    site_name = filters.site_name
    q = filters.q
    order_by = filters.order_by or "id"
    order_direction = filters.order_direction
    has_ppv = filters.has_ppv

    database_api = ua_client.database_api
    offset = (page - 1) * limit

    async with database_api.create_site_api(site_name) as site_db_api:
        session = site_db_api.get_session()

        if category:
            if category == "posts":
                content_model = PostModel
            elif category == "messages":
                content_model = MessageModel
            else:
                # This path should ideally not be taken due to Literal type hint
                raise HTTPException(status_code=400, detail="Invalid content type")

            # Build base conditions that will be reused for both queries
            base_conditions: list[Any] = []

            if user_id:
                base_conditions.append(content_model.user_id == user_id)
            if q:
                base_conditions.append(content_model.text.ilike(f"%{q}%"))
            if has_ppv is not None:
                base_conditions.append(
                    content_model.price > 0 if has_ppv else content_model.price == 0
                )

            # Handle media filter efficiently
            if has_media is not None:
                assoc = ContentMediaAssoModel.__table__
                if category == "posts":
                    content_fk = assoc.c.post_id
                elif category == "messages":
                    content_fk = assoc.c.message_id

                # Create a more efficient media check
                media_with_url_ids = select(MediaModel.id).where(
                    MediaModel.url.is_not(None)
                )

                media_exists = exists().where(
                    content_fk == content_model.id,
                    assoc.c.media_id.in_(media_with_url_ids),
                )

                if has_media:
                    base_conditions.append(media_exists)
                else:
                    base_conditions.append(~media_exists)

            # COUNT query - use base conditions directly (much faster)
            total_stmt = select(func.count(content_model.id)).where(*base_conditions)
            total = await session.scalar(total_stmt)
            assert total is not None, "Total count should not be None"

            # Main query - start with base conditions
            stmt = select(content_model).where(*base_conditions)
            # Always order by exact match first
            ordering: list[
                Case[Any]
                | UnaryExpression[datetime | int]
                | InstrumentedAttribute[datetime]
                | InstrumentedAttribute[int]
                | KeyedColumnElement[Any]
            ] = []

            if filters.prioritize_exact_match:
                ordering.append(
                    case((func.lower(content_model.text) == q.lower(), 0), else_=1)
                )

            # Field-based ordering
            if order_by in ["id", "user_id"]:
                field = content_model.id if order_by == "id" else content_model.user_id
                if order_direction.lower() == "desc":
                    ordering.append(nullslast(field.desc()))
                else:
                    ordering.append(field)

            # Always order by id last
            ordering.append(content_model.id)

            # Apply ordering and pagination
            stmt = stmt.order_by(*ordering).offset(offset).limit(limit)

            # Don't load media relationships for performance
            stmt = stmt.options(orm.selectinload(content_model.user)).options(
                orm.noload(content_model.media)
            )

            results = await session.scalars(stmt)
            content_items = results.unique().all()

            return PaginatedResponse[dict[str, Any]](
                total=total, results=jsonable_encoder(content_items)
            )
        else:
            # Category is None, search both posts and messages
            def get_conditions(model: type[PostModel] | type[MessageModel]):
                conditions: list[Any] = []
                if user_id:
                    conditions.append(model.user_id == user_id)
                if q:
                    conditions.append(model.text.ilike(f"%{q}%"))
                if has_ppv is not None:
                    conditions.append(model.price > 0 if has_ppv else model.price == 0)
                if has_media is not None:
                    assoc = ContentMediaAssoModel.__table__
                    content_fk = None
                    if model == PostModel:
                        content_fk = assoc.c.post_id
                    elif model == MessageModel:
                        content_fk = assoc.c.message_id

                    if content_fk is not None:
                        media_with_url_ids = select(MediaModel.id).where(
                            MediaModel.url.is_not(None)
                        )
                        media_exists = exists().where(
                            content_fk == model.id,
                            assoc.c.media_id.in_(media_with_url_ids),
                        )
                        if has_media:
                            conditions.append(media_exists)
                        else:
                            conditions.append(~media_exists)
                return conditions

            post_conditions = get_conditions(PostModel)
            message_conditions = get_conditions(MessageModel)

            post_total_stmt = select(func.count(PostModel.id)).where(*post_conditions)
            message_total_stmt = select(func.count(MessageModel.id)).where(
                *message_conditions
            )
            post_total = await session.scalar(post_total_stmt)
            message_total = await session.scalar(message_total_stmt)
            total = (post_total or 0) + (message_total or 0)

            post_stmt = select(
                PostModel.id,
                PostModel.text,
                PostModel.price,
                PostModel.user_id,
                literal("post").label("type"),
            ).where(*post_conditions)
            message_stmt = select(
                MessageModel.id,
                MessageModel.text,
                MessageModel.price,
                MessageModel.user_id,
                literal("message").label("type"),
            ).where(*message_conditions)

            union_stmt = union_all(post_stmt, message_stmt).alias("content")

            ordering: list[
                Case[Any]
                | UnaryExpression[datetime | int]
                | InstrumentedAttribute[datetime]
                | InstrumentedAttribute[int]
                | KeyedColumnElement[Any]
            ] = []
            if filters.prioritize_exact_match and q:
                ordering.append(
                    case((func.lower(union_stmt.c.text) == q.lower(), 0), else_=1)
                )

            if order_by in ["id", "user_id"]:
                field = union_stmt.c.id if order_by == "id" else union_stmt.c.user_id
                if order_direction.lower() == "desc":
                    ordering.append(nullslast(field.desc()))
                else:
                    ordering.append(field)

            ordering.append(union_stmt.c.id)

            stmt = select(union_stmt).order_by(*ordering).offset(offset).limit(limit)
            results = await session.execute(stmt)
            content_items = [dict(row) for row in results.mappings()]

            # Fetch and attach user objects
            user_ids = {item["user_id"] for item in content_items}
            if user_ids:
                users_stmt = select(UserModel).where(UserModel.id.in_(user_ids))
                users_result = await session.scalars(users_stmt)
                users_map = {
                    user.id: jsonable_encoder(user) for user in users_result.unique()
                }
                for item in content_items:
                    item["user"] = users_map.get(item["user_id"])

            return PaginatedResponse[dict[str, Any]](
                total=total, results=jsonable_encoder(content_items)
            )
