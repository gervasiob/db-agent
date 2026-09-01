from app.api.v1.health import router as health_router
from app.api.v1.database import router as database_router
from app.api.v1.discovery import router as discovery_router
from app.api.v1.query import router as query_router
from app.api.v1.context import router as context_router
from fastapi import APIRouter

api_router = APIRouter()
api_router.include_router(health_router, tags=["health"])
api_router.include_router(database_router, tags=["database"])
api_router.include_router(discovery_router, tags=["discovery"])
api_router.include_router(query_router, tags=["query"])
api_router.include_router(context_router, tags=["context"])

__all__ = ["api_router"]
