from __future__ import annotations

import contextvars
import datetime
import json
import logging
import sys
from typing import Any, Mapping, MutableMapping, Optional


_request_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "request_id", default=None
)


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id_var.get(None)
        return True


class JsonFormatter(logging.Formatter):
    RESERVED_ATTRS = frozenset(
        {
            "args",
            "asctime",
            "created",
            "exc_info",
            "exc_text",
            "filename",
            "funcName",
            "levelname",
            "levelno",
            "lineno",
            "module",
            "msecs",
            "message",
            "msg",
            "name",
            "pathname",
            "process",
            "processName",
            "relativeCreated",
            "stack_info",
            "thread",
            "threadName",
            "taskName",
            "request_id",
        }
    )

    def usesTime(self) -> bool:
        return True

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        if record.exc_info:
            exc_text = self.formatException(record.exc_info)
        else:
            exc_text = record.exc_text if hasattr(record, "exc_text") and record.exc_text else None

        log_entry: MutableMapping[str, Any] = {
            "timestamp": datetime.datetime.now(datetime.timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": message,
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "process_id": record.process,
            "thread_id": record.thread,
            "thread_name": record.threadName,
            "request_id": getattr(record, "request_id", None),
        }

        if exc_text:
            log_entry["exception"] = exc_text

        if record.stack_info:
            log_entry["stack_info"] = self.formatStack(record.stack_info)

        for key, value in vars(record).items():
            if key not in self.RESERVED_ATTRS and not key.startswith("_"):
                log_entry[key] = self._make_serializable(value)

        return json.dumps(log_entry, ensure_ascii=False, default=str)

    def _make_serializable(self, value: Any) -> Any:
        if isinstance(value, (str, int, float, bool, type(None))):
            return value
        if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
            return value.isoformat()
        if isinstance(value, (list, tuple)):
            return [self._make_serializable(v) for v in value]
        if isinstance(value, dict):
            return {k: self._make_serializable(v) for k, v in value.items()}
        return str(value)


class TextFormatter(logging.Formatter):
    DEFAULT_FMT = "%(asctime)s | %(levelname)-8s | %(name)s | %(request_id)s | %(message)s"

    def format(self, record: logging.LogRecord) -> str:
        if not hasattr(record, "request_id") or record.request_id is None:
            record.request_id = "-"
        return super().format(record)


class ContextLoggerAdapter(logging.LoggerAdapter):
    def process(self, msg: str, kwargs: MutableMapping[str, Any]) -> tuple[str, MutableMapping[str, Any]]:
        extra = kwargs.setdefault("extra", {})
        if self.extra:
            for key, value in self.extra.items():
                if key not in extra:
                    extra[key] = value
        return msg, kwargs

    def bind(self, **kwargs: Any) -> "ContextLoggerAdapter":
        merged: MutableMapping[str, Any] = {}
        merged.update(self.extra or {})
        merged.update(kwargs)
        return ContextLoggerAdapter(self.logger, merged)

    def unbind(self, *keys: str) -> "ContextLoggerAdapter":
        merged: MutableMapping[str, Any] = {}
        if self.extra:
            merged.update({k: v for k, v in self.extra.items() if k not in keys})
        return ContextLoggerAdapter(self.logger, merged)


def get_request_id() -> Optional[str]:
    return _request_id_var.get(None)


def set_request_id(request_id: str) -> None:
    _request_id_var.set(request_id)


def clear_request_id() -> None:
    _request_id_var.set(None)


def _get_formatter(log_format: str) -> logging.Formatter:
    if log_format == "json":
        return JsonFormatter()
    return TextFormatter(
        fmt=TextFormatter.DEFAULT_FMT,
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )


def setup_logging(
    log_level: str = "INFO",
    log_format: str = "json",
    *,
    force: bool = True,
) -> None:
    from app.core.config import settings as _settings

    level_name = (log_level or _settings.LOG_LEVEL).upper()
    fmt = log_format or _settings.LOG_FORMAT

    root_logger = logging.getLogger()
    if force:
        for handler in list(root_logger.handlers):
            root_logger.removeHandler(handler)
            handler.close()
    if root_logger.handlers:
        return

    level = logging.getLevelName(level_name)
    if not isinstance(level, int):
        level = logging.INFO
    root_logger.setLevel(level)

    stream_handler = logging.StreamHandler(stream=sys.stdout)
    stream_handler.setLevel(level)
    stream_handler.setFormatter(_get_formatter(fmt))
    stream_handler.addFilter(RequestIdFilter())
    root_logger.addHandler(stream_handler)

    for noisy in ("uvicorn.access", "httpcore", "httpx", "urllib3", "sqlalchemy.engine.Engine"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str, **extra: Any) -> ContextLoggerAdapter:
    logger = logging.getLogger(name)
    if not logger.handlers and not logging.getLogger().handlers:
        setup_logging()
    return ContextLoggerAdapter(logger, extra)
