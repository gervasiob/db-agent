from __future__ import annotations

from typing import TYPE_CHECKING, Type

from app.core.exceptions import DatabaseDiscoveryError
from app.database.adapters.base import DatabaseAdapter
from app.database.adapters.postgresql import PostgreSQLAdapter

if TYPE_CHECKING:
    from app.database.adapters.sqlserver import SQLServerAdapter


def adapter_factory(database_type: str) -> Type[DatabaseAdapter]:
    registry: dict[str, Type[DatabaseAdapter]] = {
        "postgresql": PostgreSQLAdapter,
    }
    try:
        from app.database.adapters.sqlserver import SQLServerAdapter as _SS  # noqa: F811
        registry["sqlserver"] = _SS
    except Exception:
        pass
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
    "SQLServerAdapter",
    "adapter_factory",
]
