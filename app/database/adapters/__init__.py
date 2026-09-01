from __future__ import annotations

from typing import Type

from app.core.exceptions import DatabaseDiscoveryError
from app.database.adapters.base import DatabaseAdapter
from app.database.adapters.postgresql import PostgreSQLAdapter


def adapter_factory(database_type: str) -> Type[DatabaseAdapter]:
    registry: dict[str, Type[DatabaseAdapter]] = {
        "postgresql": PostgreSQLAdapter,
    }
    adapter_cls = registry.get(database_type.lower())
    if adapter_cls is None:
        raise DatabaseDiscoveryError(
            detail=f"Unsupported database type: {database_type}",
            extra={
                "database_type": database_type,
                "supported": sorted(registry.keys()),
            },
        )
    return adapter_cls


__all__ = [
    "DatabaseAdapter",
    "PostgreSQLAdapter",
    "adapter_factory",
]
