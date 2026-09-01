from __future__ import annotations

import datetime
from typing import Any, MutableMapping, Optional

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import ORJSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp

from app.core.logging import get_logger, get_request_id


logger = get_logger(__name__)


class DBAgentException(Exception):
    def __init__(
        self,
        code: str,
        detail: str,
        status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR,
        *,
        extra: Optional[MutableMapping[str, Any]] = None,
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status_code = status_code
        self.extra: MutableMapping[str, Any] = dict(extra or {})

    def to_dict(self) -> MutableMapping[str, Any]:
        payload: MutableMapping[str, Any] = {
            "type": "about:blank",
            "title": self.code,
            "status": self.status_code,
            "detail": self.detail,
            "instance": None,
            "timestamp": datetime.datetime.now(datetime.timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "request_id": get_request_id(),
        }
        if self.extra:
            payload["extra"] = dict(self.extra)
        return payload


class DatabaseConnectionError(DBAgentException):
    def __init__(
        self,
        detail: str = "Failed to connect to the target database",
        *,
        extra: Optional[MutableMapping[str, Any]] = None,
    ) -> None:
        super().__init__(
            code="DATABASE_CONNECTION_ERROR",
            detail=detail,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            extra=extra,
        )


class DatabaseDiscoveryError(DBAgentException):
    def __init__(
        self,
        detail: str = "Database discovery operation failed",
        *,
        extra: Optional[MutableMapping[str, Any]] = None,
    ) -> None:
        super().__init__(
            code="DATABASE_DISCOVERY_ERROR",
            detail=detail,
            status_code=status.HTTP_502_BAD_GATEWAY,
            extra=extra,
        )


class SchemaRetrievalError(DBAgentException):
    def __init__(
        self,
        detail: str = "Failed to retrieve schema information",
        *,
        extra: Optional[MutableMapping[str, Any]] = None,
    ) -> None:
        super().__init__(
            code="SCHEMA_RETRIEVAL_ERROR",
            detail=detail,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            extra=extra,
        )


class LLMGenerationError(DBAgentException):
    def __init__(
        self,
        detail: str = "LLM generation failed",
        *,
        extra: Optional[MutableMapping[str, Any]] = None,
    ) -> None:
        super().__init__(
            code="LLM_GENERATION_ERROR",
            detail=detail,
            status_code=status.HTTP_502_BAD_GATEWAY,
            extra=extra,
        )


class SQLValidationError(DBAgentException):
    def __init__(
        self,
        reason: str,
        raw_sql: str,
        *,
        extra: Optional[MutableMapping[str, Any]] = None,
    ) -> None:
        merged: MutableMapping[str, Any] = dict(extra or {})
        merged.setdefault("reason", reason)
        merged.setdefault("raw_sql", raw_sql)
        super().__init__(
            code="SQL_VALIDATION_ERROR",
            detail=f"Generated SQL is invalid: {reason}",
            status_code=422,
            extra=merged,
        )

    @property
    def reason(self) -> str:
        return str(self.extra.get("reason", ""))

    @property
    def raw_sql(self) -> str:
        return str(self.extra.get("raw_sql", ""))


class SQLExecutionError(DBAgentException):
    def __init__(
        self,
        detail: str = "SQL execution failed",
        *,
        extra: Optional[MutableMapping[str, Any]] = None,
    ) -> None:
        super().__init__(
            code="SQL_EXECUTION_ERROR",
            detail=detail,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            extra=extra,
        )


class SemanticAnalysisError(DBAgentException):
    def __init__(
        self,
        detail: str = "Semantic analysis of the user question failed",
        *,
        extra: Optional[MutableMapping[str, Any]] = None,
    ) -> None:
        super().__init__(
            code="SEMANTIC_ANALYSIS_ERROR",
            detail=detail,
            status_code=422,
            extra=extra,
        )


class ContextNotFoundError(DBAgentException):
    def __init__(
        self,
        detail: str = "Requested context was not found",
        *,
        resource_type: Optional[str] = None,
        resource_id: Optional[Any] = None,
        extra: Optional[MutableMapping[str, Any]] = None,
    ) -> None:
        merged: MutableMapping[str, Any] = dict(extra or {})
        if resource_type is not None:
            merged.setdefault("resource_type", resource_type)
        if resource_id is not None:
            merged.setdefault("resource_id", resource_id)
        super().__init__(
            code="CONTEXT_NOT_FOUND_ERROR",
            detail=detail,
            status_code=status.HTTP_404_NOT_FOUND,
            extra=merged,
        )


class InvalidInputError(DBAgentException):
    def __init__(
        self,
        detail: str = "The provided input is invalid",
        *,
        field: Optional[str] = None,
        extra: Optional[MutableMapping[str, Any]] = None,
    ) -> None:
        merged: MutableMapping[str, Any] = dict(extra or {})
        if field is not None:
            merged.setdefault("field", field)
        super().__init__(
            code="INVALID_INPUT_ERROR",
            detail=detail,
            status_code=status.HTTP_400_BAD_REQUEST,
            extra=merged,
        )


class SecurityError(DBAgentException):
    def __init__(
        self,
        detail: str = "A security policy prevented the requested operation",
        *,
        extra: Optional[MutableMapping[str, Any]] = None,
    ) -> None:
        super().__init__(
            code="SECURITY_ERROR",
            detail=detail,
            status_code=status.HTTP_403_FORBIDDEN,
            extra=extra,
        )


def _make_response(
    body: MutableMapping[str, Any],
    status_code: int,
) -> ORJSONResponse:
    return ORJSONResponse(status_code=status_code, content=body)


def _extract_path(request: Request) -> str:
    try:
        return getattr(request, "url", None).path or ""
    except Exception:
        return ""


async def db_agent_exception_handler(request: Request, exc: DBAgentException) -> ORJSONResponse:
    body = exc.to_dict()
    body["instance"] = _extract_path(request)
    logger.warning(
        "DBAgentException raised: %s",
        exc.code,
        extra={
            "exception_code": exc.code,
            "status_code": exc.status_code,
            "path": body["instance"],
        },
        exc_info=exc,
    )
    return _make_response(body, exc.status_code)


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> ORJSONResponse:
    body: MutableMapping[str, Any] = {
        "type": "about:blank",
        "title": "HTTP_ERROR",
        "status": exc.status_code,
        "detail": exc.detail if isinstance(exc.detail, str) else str(exc.detail),
        "instance": _extract_path(request),
        "timestamp": datetime.datetime.now(datetime.timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
        "request_id": get_request_id(),
    }
    if hasattr(exc, "headers") and exc.headers:
        body["headers"] = dict(exc.headers)
    if exc.status_code >= 500:
        logger.error("HTTP exception: %s %s", exc.status_code, exc.detail, exc_info=exc)
    elif exc.status_code >= 400:
        logger.warning("HTTP exception: %s %s", exc.status_code, exc.detail)
    return _make_response(body, exc.status_code)


def _flatten_pydantic_errors(exc: RequestValidationError | ValidationError) -> list[MutableMapping[str, Any]]:
    issues: list[MutableMapping[str, Any]] = []
    for err in exc.errors():
        loc = err.get("loc", ())
        issues.append(
            {
                "field": ".".join(str(part) for part in loc) if loc else None,
                "message": err.get("msg", str(err)),
                "type": err.get("type"),
                "ctx": err.get("ctx"),
            }
        )
    return issues


async def request_validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> ORJSONResponse:
    issues = _flatten_pydantic_errors(exc)
    body: MutableMapping[str, Any] = {
        "type": "about:blank",
        "title": "VALIDATION_ERROR",
        "status": 422,
        "detail": "Request validation failed",
        "instance": _extract_path(request),
        "timestamp": datetime.datetime.now(datetime.timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
        "request_id": get_request_id(),
        "errors": issues,
    }
    logger.warning(
        "Request validation failed with %d issue(s)",
        len(issues),
        extra={"errors": issues},
    )
    return _make_response(body, 422)


async def pydantic_validation_exception_handler(
    request: Request,
    exc: ValidationError,
) -> ORJSONResponse:
    issues = _flatten_pydantic_errors(exc)
    body: MutableMapping[str, Any] = {
        "type": "about:blank",
        "title": "VALIDATION_ERROR",
        "status": status.HTTP_500_INTERNAL_SERVER_ERROR,
        "detail": "Internal validation failure",
        "instance": _extract_path(request),
        "timestamp": datetime.datetime.now(datetime.timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
        "request_id": get_request_id(),
        "errors": issues,
    }
    logger.error(
        "Internal Pydantic validation error with %d issue(s)",
        len(issues),
        extra={"errors": issues},
        exc_info=exc,
    )
    return _make_response(body, status.HTTP_500_INTERNAL_SERVER_ERROR)


async def unhandled_exception_handler(request: Request, exc: Exception) -> ORJSONResponse:
    body: MutableMapping[str, Any] = {
        "type": "about:blank",
        "title": "INTERNAL_SERVER_ERROR",
        "status": status.HTTP_500_INTERNAL_SERVER_ERROR,
        "detail": "An unexpected error occurred while processing your request",
        "instance": _extract_path(request),
        "timestamp": datetime.datetime.now(datetime.timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
        "request_id": get_request_id(),
    }
    logger.error(
        "Unhandled exception: %s",
        type(exc).__name__,
        extra={"exception_type": type(exc).__name__},
        exc_info=exc,
    )
    return _make_response(body, status.HTTP_500_INTERNAL_SERVER_ERROR)


def register_exception_handlers(app: ASGIApp) -> None:
    if not isinstance(app, FastAPI):
        return
    app.add_exception_handler(DBAgentException, db_agent_exception_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, request_validation_exception_handler)
    app.add_exception_handler(ValidationError, pydantic_validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
