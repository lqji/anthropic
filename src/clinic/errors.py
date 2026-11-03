import logging

import psycopg
import psycopg_pool
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from clinic.api.schemas import ErrorBody

logger = logging.getLogger("clinic.errors")


class AppError(Exception):
    """Base class for errors that map to a JSON error response."""

    status_code = 500
    code = "internal_error"

    def __init__(self, detail: str | None = None) -> None:
        self.detail = detail
        super().__init__(detail or self.code)


class NotFound(AppError):
    status_code = 404
    code = "not_found"


class InvalidRequest(AppError):
    """A request that is well-formed but breaks a rule (e.g. a date in the past)."""

    status_code = 422
    code = "validation_error"


class ConversationBusy(AppError):
    status_code = 409
    code = "conversation_busy"


class DatabaseUnavailable(AppError):
    status_code = 503
    code = "database_unavailable"


class LLMUnavailable(AppError):
    status_code = 503
    code = "llm_unavailable"


_HTTP_CODES = {404: "not_found", 405: "method_not_allowed"}


def error_response(
    request: Request, status_code: int, code: str, detail: str | None
) -> JSONResponse:
    body = ErrorBody(
        code=code, detail=detail, request_id=getattr(request.state, "request_id", None)
    )
    return JSONResponse(status_code=status_code, content=body.model_dump(exclude_none=True))


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        if exc.status_code >= 500:
            logger.error("app_error", extra={"fields": {"code": exc.code, "detail": exc.detail}})
        return error_response(request, exc.status_code, exc.code, exc.detail)

    @app.exception_handler(StarletteHTTPException)
    def handle_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, f"http_{exc.status_code}")
        return error_response(request, exc.status_code, code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        detail = "; ".join(
            f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        return error_response(request, 422, "validation_error", detail)

    @app.exception_handler(psycopg.OperationalError)
    @app.exception_handler(psycopg_pool.PoolTimeout)
    def handle_database_error(request: Request, exc: Exception) -> JSONResponse:
        logger.error("database_unavailable", extra={"fields": {"error": type(exc).__name__}})
        return error_response(request, 503, DatabaseUnavailable.code, "database unavailable")
