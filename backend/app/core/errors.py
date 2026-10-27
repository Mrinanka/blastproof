"""Errors in CLAUDE.md's shape: {"error": {"code": "...", "message": "..."}}.

Routes and dependencies raise ApiError; the handler turns it into the response. Invalid input
(422) uses the same shape and never repeats what was sent, which may be a password (brought
forward from step 19, D-094). Starlette's own errors (an unknown path is 404, a wrong method is
405) use it too.
"""

from collections.abc import Callable, Mapping
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

# Enough to fix a form; a request with hundreds of errors must not produce a huge message.
_MAX_VALIDATION_ERRORS = 5
_MAX_MESSAGE_LENGTH = 400


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = dict(headers or {})


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    """The error body, for routes to declare in their OpenAPI `responses`."""

    error: ErrorDetail


async def api_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    return JSONResponse(
        {"error": {"code": exc.code, "message": exc.message}},
        status_code=exc.status_code,
        headers=exc.headers,
    )


_HTTP_ERRORS = {
    404: ("not_found", "No such route."),
    405: ("method_not_allowed", "That method is not allowed on this route."),
}


async def http_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Starlette's own errors (no such route, wrong method) in the envelope.

    The `detail` text is not repeated. Headers such as `Allow` on a 405 are kept.
    """
    assert isinstance(exc, StarletteHTTPException)
    code, message = _HTTP_ERRORS.get(exc.status_code, ("http_error", "The request failed."))
    return JSONResponse(
        {"error": {"code": code, "message": message}},
        status_code=exc.status_code,
        headers=exc.headers,
    )


async def validation_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    """422 in the envelope: where and what is wrong, never the value that was sent.

    FastAPI's default body includes each error's `input`, which for a login is the password.
    Pydantic's `msg` texts describe the rule (e.g. "String should have at most 256 characters").
    """
    assert isinstance(exc, RequestValidationError)
    errors = exc.errors()
    parts = [
        f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
        for error in errors[:_MAX_VALIDATION_ERRORS]
    ]
    if len(errors) > _MAX_VALIDATION_ERRORS:
        parts.append(f"and {len(errors) - _MAX_VALIDATION_ERRORS} more")
    message = ("Invalid request: " + "; ".join(parts))[:_MAX_MESSAGE_LENGTH] + "."
    return JSONResponse(
        {"error": {"code": "validation_error", "message": message}}, status_code=422
    )


def use_error_envelope_in_openapi(app: FastAPI) -> None:
    """Make /openapi.json describe 422 responses as ErrorResponse, as they really are.

    The dashboard generates its types from this schema, so it must match the handler above.
    """
    build_default: Callable[[], dict[str, Any]] = app.openapi

    def openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = build_default()
        ref = {"$ref": "#/components/schemas/ErrorResponse"}
        for path in schema.get("paths", {}).values():
            for operation in path.values():
                response = operation.get("responses", {}).get("422")
                if response is not None:
                    response["description"] = "Invalid request"
                    response["content"] = {"application/json": {"schema": ref}}
        components = schema.setdefault("components", {}).setdefault("schemas", {})
        components.pop("HTTPValidationError", None)
        components.pop("ValidationError", None)
        error_schema = ErrorResponse.model_json_schema(ref_template="#/components/schemas/{model}")
        components.update(error_schema.pop("$defs", {}))
        components.setdefault("ErrorResponse", error_schema)
        app.openapi_schema = schema
        return schema

    app.openapi = openapi  # type: ignore[method-assign]
