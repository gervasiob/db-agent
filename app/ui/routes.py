from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.core.config import settings

ui_router = APIRouter(tags=["ui"], include_in_schema=False)

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
_jinja_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
    enable_async=True,
)


async def _render(template_name: str, context: dict[str, Any]) -> str:
    tpl = _jinja_env.get_template(template_name)
    return await tpl.render_async(**context)


@ui_router.get("/ui", response_class=HTMLResponse)
async def ui_home(request: Request) -> HTMLResponse:
    versions = {
        "app_version": settings.APP_VERSION,
        "app_name": settings.APP_NAME,
        "app_env": settings.APP_ENV,
        "llm_model": settings.OPENAI_MODEL,
        "embedding_model": settings.OPENAI_EMBEDDING_MODEL,
        "max_query_rows": settings.MAX_QUERY_ROWS,
        "query_timeout_seconds": settings.QUERY_TIMEOUT_SECONDS,
        "sql_retry_count": settings.SQL_RETRY_COUNT,
        "schema_retrieval_top_k": settings.SCHEMA_RETRIEVAL_TOP_K,
        "llm_include_sample_data": settings.LLM_INCLUDE_SAMPLE_DATA,
        "llm_sanitize_sample_data": settings.LLM_SANITIZE_SAMPLE_DATA,
    }
    html = await _render("ui.html", {"request": request, "versions": versions})
    return HTMLResponse(html)


@ui_router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def ui_root(request: Request) -> HTMLResponse:
    return await ui_home(request)

