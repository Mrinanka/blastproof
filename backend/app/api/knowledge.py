from typing import Annotated, Any, Literal

import structlog
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import PageParams, get_knowledge
from app.core.auth import CurrentUser, get_current_user, require_admin
from app.core.errors import ApiError, ErrorResponse
from app.db import knowledge as knowledge_db
from app.db.session import get_session
from app.schemas.knowledge import KnowledgeChanged, KnowledgePage, KnowledgeSource
from app.services import uploads
from app.services.knowledge import (
    SCHEMA_DOCS_SOURCE,
    SCHEMA_SCAN_SOURCE,
    DocumentError,
    KnowledgeService,
    split_document,
)

router = APIRouter(prefix="/v1/knowledge", tags=["knowledge"])
logger = structlog.get_logger()

# The body is parsed by hand (see services/uploads.py), so OpenAPI is told about it here.
_UPLOAD_BODY: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["file"],
                    "properties": {
                        "file": {"type": "string", "format": "binary"},
                        "tables": {"type": "array", "items": {"type": "string"}},
                    },
                }
            }
        },
    }
}


def _kind(source: str) -> Literal["schema_scan", "schema_docs", "upload"]:
    if source == SCHEMA_SCAN_SOURCE:
        return "schema_scan"
    return "schema_docs" if source == SCHEMA_DOCS_SOURCE else "upload"


@router.get(
    "",
    response_model=KnowledgePage,
    responses={401: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def list_knowledge(
    page: Annotated[PageParams, Query()],
    _user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> KnowledgePage:
    """The sources the AI reads when it explains a change, by name. Shared by everyone."""
    rows, total = await knowledge_db.list_sources(session, limit=page.limit, offset=page.offset)
    return KnowledgePage(
        items=[
            KnowledgeSource(
                source=row.source,
                kind=_kind(row.source),
                chunks=row.chunks,
                table_names=list(row.table_names),
                updated_at=row.updated_at,
            )
            for row in rows
        ],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post(
    "",
    response_model=KnowledgeChanged,
    status_code=201,
    openapi_extra=_UPLOAD_BODY,
    responses={code: {"model": ErrorResponse} for code in (401, 403, 411, 413, 422, 503)},
)
async def upload_knowledge(
    request: Request,
    _admin: Annotated[CurrentUser, Depends(require_admin)],
    knowledge: Annotated[KnowledgeService, Depends(get_knowledge)],
) -> KnowledgeChanged:
    """Add a .md or .txt document (admins only). The same name again replaces it."""
    upload = await uploads.read_upload(request)
    try:
        drafts = split_document(upload.name, upload.data, upload.tables)
    except DocumentError as exc:
        raise uploads.invalid(str(exc)) from None
    if not drafts:
        # Ingesting nothing would delete the source, so an empty document is refused.
        raise uploads.invalid("The document has no text.")
    try:
        chunks = await knowledge.ingest_drafts(upload.name, drafts)
    except Exception as exc:
        # Embedding or the vector store failed. Only the class is logged, never the document.
        logger.error("knowledge_upload_failed", reason_code=type(exc).__name__)
        raise ApiError(503, "unavailable", "The document could not be indexed.") from None
    return KnowledgeChanged(source=upload.name, chunks=chunks)


@router.delete(
    "/{source}",
    response_model=KnowledgeChanged,
    responses={code: {"model": ErrorResponse} for code in (401, 403, 404, 503)},
)
async def delete_knowledge(
    source: str,
    _admin: Annotated[CurrentUser, Depends(require_admin)],
    knowledge: Annotated[KnowledgeService, Depends(get_knowledge)],
) -> KnowledgeChanged:
    """Remove a source and its chunks (admins only)."""
    try:
        chunks = await knowledge.delete_source(source)
    except Exception as exc:
        logger.error("knowledge_delete_failed", reason_code=type(exc).__name__)
        raise ApiError(503, "unavailable", "The source could not be removed.") from None
    if chunks == 0:
        raise ApiError(404, "not_found", "No such knowledge source.")
    return KnowledgeChanged(source=source, chunks=chunks)
