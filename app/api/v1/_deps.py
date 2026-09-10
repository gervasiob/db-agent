from __future__ import annotations

from typing import Any, TYPE_CHECKING

from fastapi import HTTPException, Request, status

if TYPE_CHECKING:
    from app.agents.database_agent import DatabaseAgent


def _unavailable_response(request: Request) -> HTTPException:
    state = request.app.state
    metadata_ok = getattr(state, "metadata_ok", None)
    metadata_error = getattr(state, "metadata_error", None)
    agent_init_error = getattr(state, "agent_init_error", None)
    detail: dict[str, Any] = {
        "code": "agent_not_initialized",
        "metadata_ok": metadata_ok,
    }
    if metadata_error:
        detail["metadata_error"] = metadata_error
    if agent_init_error:
        detail["agent_init_error"] = agent_init_error
    if metadata_ok is False:
        hint = "Metadata DB no se pudo conectar en el startup del servidor. "
        if metadata_error:
            hint += f"Detalle: {metadata_error}. "
        hint += "Revisá que PostgreSQL esté levantado en el host/puerto de METADATA_DB_URL del .env y que la db exista con pgvector."
        detail["hint"] = hint
    elif agent_init_error:
        detail["hint"] = (
            "El servidor arrancó pero falló la instanciación del DatabaseAgent. "
            f"Detalle: {agent_init_error}."
        )
    else:
        detail["hint"] = (
            "El servidor nunca completó el lifespan startup. Probablemente el proceso uvicorn se "
            "cayó durante el bootstrap o está corriendo código viejo. Reiniciar uvicorn."
        )
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=detail,
    )


def require_agent(request: Request) -> "DatabaseAgent":
    """Devuelve el DatabaseAgent del app.state o levanta HTTP 503 con el motivo real."""
    agent = getattr(request.app.state, "agent", None)
    if agent is None:
        raise _unavailable_response(request)
    return agent


__all__ = ["require_agent"]
