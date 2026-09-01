from __future__ import annotations

import importlib
import pkgutil

import pytest


def _import_all(package_name: str) -> list[type]:
    package = importlib.import_module(package_name)
    classes_found: list[type] = []
    for mod_info in pkgutil.walk_packages(package.__path__, prefix=f"{package_name}."):
        module = importlib.import_module(mod_info.name)
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if isinstance(attr, type) and attr.__module__.startswith(package_name):
                classes_found.append(attr)
    return classes_found


_SERVICE_MODULES = [
    "app.services.database_connection_service",
    "app.services.database_context_service",
    "app.services.query_answer_service",
    "app.services.query_intent_service",
    "app.services.query_result_service",
    "app.services.semantic_query_service",
    "app.services.sql_execution_service",
    "app.services.sql_generation_service",
    "app.services.sql_validation_service",
    "app.services.data_sanitization_service",
    "app.services.column_profiling_service",
    "app.services.database_discovery_service",
    "app.services.database_sampling_service",
    "app.services.database_semantic_analyzer",
    "app.services.relationship_graph_service",
    "app.services.terminology_service",
    "app.services.dimension_service",
    "app.services.business_domain_service",
    "app.services.business_entity_service",
    "app.services.business_concept_service",
    "app.services.business_metric_service",
    "app.services.date_semantic_service",
    "app.services.schema_retrieval_service",
]


@pytest.mark.parametrize("module_name", _SERVICE_MODULES)
def test_service_module_importable(module_name: str) -> None:
    mod = importlib.import_module(module_name)
    assert mod is not None
    has_service_class = any(
        attr.endswith(("Service", "Analyzer", "Repository", "Pipeline"))
        for attr in dir(mod)
    )
    assert has_service_class, f"No Service/Analyzer/Repository/Pipeline class in {module_name}"


def test_sql_validator_instantiation(patch_settings) -> None:
    from app.security.sql_validator import SqlValidator

    v = SqlValidator()
    assert v is not None
    result = v.validate_read_only("SELECT 1", allowed_tables=[])
    assert result.is_readonly


def test_data_sanitization_instantiation(patch_settings) -> None:
    from app.services.data_sanitization_service import DataSanitizationService

    s = DataSanitizationService()
    assert s.sanitize_value("email@test.com") == "[EMAIL]"


def test_database_connection_service_instantiation(patch_settings) -> None:
    from app.services.database_connection_service import DatabaseConnectionService

    svc = DatabaseConnectionService()
    assert svc is not None
    assert svc.repository is not None


def test_database_context_service_instantiation(patch_settings) -> None:
    from app.services.database_context_service import DatabaseContextService

    svc = DatabaseContextService()
    assert svc is not None
    assert svc.llm_model is not None


def test_agent_facade_instantiation(
    patch_settings,
    fake_metadata_session,
) -> None:
    from unittest.mock import MagicMock

    from app.agents.database_agent import DatabaseAgent

    factory = MagicMock(name="session_factory")
    agent = DatabaseAgent(factory)
    assert agent is not None
    assert agent._conn_repo is not None
    assert agent._answer_service is not None
