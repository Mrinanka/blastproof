"""Reading a knowledge upload safely (SEC-32, backend half).

The route has no `File` parameter: FastAPI would parse the body before the admin check runs, and
Starlette spools file parts to disk with no cap. Instead the route checks who is calling, then this
module checks the declared length, and only then parses the body.
"""

import re
from dataclasses import dataclass

from fastapi import Request
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import ApiError
from app.services.knowledge import (
    MAX_DOCUMENT_BYTES,
    SCHEMA_SCAN_SOURCE,
    DocumentError,
    normalize_table_name,
)

MAX_BODY_BYTES = MAX_DOCUMENT_BYTES + 64 * 1024  # the file plus the multipart framing
MAX_TABLES = 20
MAX_FIELDS = MAX_TABLES + 1  # the tables and the file
# A plain file name: no path, one of two extensions. The name is the knowledge source.
SOURCE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._ -]{0,99}\.(md|txt)")


@dataclass(frozen=True)
class Upload:
    name: str
    data: bytes
    tables: tuple[str, ...]


def invalid(message: str) -> ApiError:
    # Never repeats the file name or any text from the upload.
    return ApiError(422, "invalid_upload", message)


def check_source_name(name: str | None) -> str:
    if name is None or SOURCE_NAME.fullmatch(name) is None:
        raise invalid("The file name must be a plain name ending in .md or .txt.")
    if name == SCHEMA_SCAN_SOURCE:
        raise invalid("That name is reserved.")
    return name


def check_length(header: str | None) -> None:
    """411 without a Content-Length, 413 over the limit: refused before any byte is read."""
    if header is None:
        raise ApiError(411, "length_required", "Send a Content-Length with the upload.")
    if not header.isascii() or not header.isdigit():
        raise invalid("The Content-Length is not a number.")
    if int(header) > MAX_BODY_BYTES:
        raise ApiError(413, "too_large", "The upload is larger than 1 MB.")


async def read_upload(request: Request) -> Upload:
    check_length(request.headers.get("content-length"))
    try:
        form = await request.form(max_files=1, max_fields=MAX_FIELDS)
    except (StarletteHTTPException, ValueError, KeyError):
        raise invalid("The upload could not be read as a multipart form.") from None
    try:
        files = [v for _, v in form.multi_items() if isinstance(v, UploadFile)]
        if len(files) != 1:
            raise invalid("Send exactly one file.")
        name = check_source_name(files[0].filename)
        data = await files[0].read(MAX_DOCUMENT_BYTES + 1)
        if len(data) > MAX_DOCUMENT_BYTES:
            raise invalid("The file is larger than 1 MB.")
        raw_tables = [v for v in form.getlist("tables") if isinstance(v, str)]
        if len(raw_tables) > MAX_TABLES:
            raise invalid(f"At most {MAX_TABLES} table tags.")
        try:
            tables = tuple(normalize_table_name(t) for t in raw_tables)
        except DocumentError as exc:
            raise invalid(str(exc)) from None
        return Upload(name=name, data=data, tables=tables)
    finally:
        await form.close()
