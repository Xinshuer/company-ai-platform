"""API errors in the §18 shape: {"error": {"code": ..., "message": ...}} with a 4xx/5xx status.

Messages are fixed and short and say what to do; they never echo request bodies, exception text,
SQL or tokens (validation errors from FastAPI are replaced with a generic message for that reason).
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)

_STATUS_CODES = {400: "bad_request", 401: "unauthenticated", 403: "forbidden", 404: "not_found",
                 405: "bad_request", 413: "bad_request", 415: "bad_request", 429: "budget_exceeded"}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def error_response(status: int, code: str, message: str, *, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status, headers=headers)


def unauthenticated() -> ApiError:
    return ApiError(401, "unauthenticated", "Log in first (pick a persona).")


def forbidden(message: str = "Your role may not do this.") -> ApiError:
    return ApiError(403, "forbidden", message)


def not_found(message: str = "Not found.") -> ApiError:
    return ApiError(404, "not_found", message)


def bad_request(message: str) -> ApiError:
    return ApiError(400, "bad_request", message)


def install_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, e: ApiError) -> JSONResponse:
        return error_response(e.status, e.code, e.message)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, e: RequestValidationError) -> JSONResponse:
        return error_response(400, "bad_request", "The request is malformed; check the fields and try again.")

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, e: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_CODES.get(e.status_code, "bad_request" if e.status_code < 500 else "internal_error")
        msg = "Not found." if e.status_code == 404 else "The request could not be handled."
        return error_response(e.status_code, code, msg, headers=getattr(e, "headers", None))

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, e: Exception) -> JSONResponse:
        log.warning("unhandled API error: %s", type(e).__name__)
        return error_response(500, "internal_error", "Something went wrong; try again or ask the platform admin.")
