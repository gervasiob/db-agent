from __future__ import annotations

import re
from typing import Any, Optional

from app.core.exceptions import InvalidInputError, SemanticAnalysisError
from app.core.logging import get_logger
from app.llm.client import async_generate_structured
from app.models.database import DatabaseMetadata, TableMetadata
from app.models.semantic import BusinessDomain
from pydantic import BaseModel, Field

logger = get_logger(__name__)


DOMAIN_RULE_PATTERNS: list[dict[str, Any]] = [
    {
        "code": "HR",
        "name": "Human Resources",
        "name_es": "Recursos Humanos",
        "keywords": [
            "hr", "empleado", "employee", "empleados", "employees",
            "personal", "staff", "trabajador", "worker", "colaborador",
            "salario", "salary", "payroll", "nomina", "nominas",
            "contrato", "contract", "contratacion", "hire", "departamento",
            "department", "cargo", "position", "puesto", "vacaciones",
            "benefit", "beneficio", "asistencia", "attendance",
        ],
        "aliases": ["RRHH", "Human Resources", "Recursos Humanos", "Personal"],
        "description": "Gestión de empleados, nóminas, contratos y recursos humanos",
    },
    {
        "code": "SALES",
        "name": "Sales",
        "name_es": "Ventas",
        "keywords": [
            "sale", "sales", "venta", "ventas", "order", "orders",
            "pedido", "pedidos", "orden", "ordenes", "orden_compra",
            "purchase", "compra", "compras", "cart", "carrito",
            "checkout", "factura_venta", "venta_detalle",
        ],
        "aliases": ["Ventas", "Sales", "Pedidos", "Órdenes"],
        "description": "Gestión de ventas, pedidos y órdenes de compra",
    },
    {
        "code": "CUSTOMERS",
        "name": "Customers",
        "name_es": "Clientes",
        "keywords": [
            "customer", "customers", "cliente", "clientes", "client",
            "clients", "consumer", "consumidor", "usuario", "user",
            "account_user", "customer_profile", "cliente_profile",
            "suscriptor", "subscriber", "lead", "prospecto",
        ],
        "aliases": ["Clientes", "Customers", "Usuarios", "Consumidores"],
        "description": "Gestión de clientes, usuarios y suscriptores",
    },
    {
        "code": "PRODUCTS",
        "name": "Products",
        "name_es": "Productos",
        "keywords": [
            "product", "products", "producto", "productos", "item",
            "items", "articulo", "articulos", "sku", "stock_keeping",
            "category", "categoria", "brand", "marca", "catalog",
            "catalogo", "inventory_item", "precio", "price",
        ],
        "aliases": ["Productos", "Products", "Artículos", "Catálogo"],
        "description": "Gestión de productos, artículos y catálogo",
    },
    {
        "code": "FINANCE",
        "name": "Finance",
        "name_es": "Finanzas",
        "keywords": [
            "finance", "finanzas", "invoice", "invoices", "factura",
            "facturas", "facturacion", "billing", "payment", "payments",
            "pago", "pagos", "abono", "cobro", "cobros", "receipt",
            "recibo", "comprobante", "transaction", "transaccion",
            "accounting", "contabilidad", "ledger", "libro_mayor",
            "tax", "impuesto", "iva", "gst",
        ],
        "aliases": ["Finanzas", "Finance", "Facturación", "Contabilidad"],
        "description": "Gestión financiera, facturación, pagos y contabilidad",
    },
    {
        "code": "INVENTORY",
        "name": "Inventory",
        "name_es": "Inventario",
        "keywords": [
            "inventory", "inventario", "stock", "almacen", "warehouse",
            "deposito", "location", "ubicacion", "bin", "lote", "batch",
            "serial", "serie", "receiving", "recepcion", "mercadancia",
            "goods", "transfer", "transferencia", "movement", "movimiento",
        ],
        "aliases": ["Inventario", "Inventory", "Stock", "Almacén"],
        "description": "Gestión de inventarios, stock y almacenes",
    },
    {
        "code": "BILLING",
        "name": "Billing",
        "name_es": "Facturación",
        "keywords": [
            "billing", "facturacion", "invoice", "factura", "bill",
            "cuenta", "account_receivable", "cuentas_por_cobrar",
            "subscription", "suscripcion", "plan", "membership",
            "membresia", "recurring", "recurrente", "charge", "cargo",
        ],
        "aliases": ["Facturación", "Billing", "Cobros", "Suscripciones"],
        "description": "Gestión de facturación recurrente, suscripciones y cobros",
    },
    {
        "code": "LOGISTICS",
        "name": "Logistics",
        "name_es": "Logística",
        "keywords": [
            "logistic", "logistica", "ship", "shipping", "envio",
            "envios", "shipment", "despacho", "delivery", "entrega",
            "carrier", "transportista", "courier", "correo",
            "tracking", "seguimiento", "package", "paquete",
            "parcel", "freight", "flete", "logistics",
        ],
        "aliases": ["Logística", "Logistics", "Envíos", "Despachos"],
        "description": "Gestión logística, envíos y entregas",
    },
    {
        "code": "AUDIT",
        "name": "Audit",
        "name_es": "Auditoría",
        "keywords": [
            "audit", "auditoria", "log", "logs", "bitacora",
            "activity", "actividad", "event", "evento", "history",
            "historial", "change_log", "cambio", "record", "registro",
            "access", "acceso", "security_log", "login", "sesion",
        ],
        "aliases": ["Auditoría", "Audit", "Logs", "Bitácora"],
        "description": "Registros de auditoría, logs y actividades del sistema",
    },
    {
        "code": "CONFIGURATION",
        "name": "Configuration",
        "name_es": "Configuración",
        "keywords": [
            "config", "configuration", "configuracion", "param",
            "parameter", "parametro", "setting", "settings",
            "opcion", "option", "preference", "preferencia",
            "system_config", "property", "propiedad", "tenant",
            "organization", "organizacion", "empresa", "company",
        ],
        "aliases": ["Configuración", "Configuration", "Parámetros", "Settings"],
        "description": "Configuración del sistema, parámetros y opciones",
    },
]


def _snake_case_upper(text: str) -> str:
    text = text.strip()
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", text)
    s2 = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1)
    s3 = re.sub(r"[^a-zA-Z0-9]+", "_", s2)
    result = s3.strip("_").upper()
    if not result:
        result = re.sub(r"[^A-Z0-9]+", "_", text.upper()).strip("_")
    return result or "UNKNOWN"


class _LLMDomainItem(BaseModel):
    name: str
    description: str = ""
    aliases: list[str] = Field(default_factory=list)
    tables: list[str] = Field(default_factory=list)
    confidence: float = 1.0


class _LLMDomainList(BaseModel):
    domains: list[_LLMDomainItem] = Field(default_factory=list)


class BusinessDomainService:
    def __init__(
        self,
        semantic_repository: Any = None,
        llm_enabled: bool = True,
    ) -> None:
        self.semantic_repository = semantic_repository
        self.llm_enabled = llm_enabled
        logger.info(
            "BusinessDomainService initialized",
            extra={"llm_enabled": llm_enabled, "has_repository": semantic_repository is not None},
        )

    async def detect_domains(
        self,
        database_metadata: DatabaseMetadata,
        table_analyses: list[Any] | None = None,
        concepts_hint: Optional[list[str]] = None,
    ) -> list[BusinessDomain]:
        if not database_metadata:
            raise InvalidInputError(
                detail="database_metadata is required for domain detection",
                field="database_metadata",
            )

        logger.info(
            "Starting business domain detection",
            extra={
                "schema_count": len(database_metadata.schemas),
                "has_hints": bool(concepts_hint),
                "llm_enabled": self.llm_enabled,
            },
        )

        all_tables = self._flatten_tables(database_metadata)
        table_names_lower: dict[str, str] = {}
        table_comments: dict[str, str] = {}
        for t in all_tables:
            full_name = f"{t.schema_name}.{t.table_name}"
            table_names_lower[full_name.lower()] = full_name
            if t.comment:
                table_comments[full_name] = t.comment.lower()

        domains_by_code: dict[str, BusinessDomain] = {}

        try:
            llm_domains: list[_LLMDomainItem] = []
            if self.llm_enabled:
                llm_domains = await self._detect_domains_llm(
                    all_tables,
                    concepts_hint or [],
                )
            else:
                logger.debug("LLM disabled, skipping LLM domain detection")

            for llm_item in llm_domains:
                name = (llm_item.name or "").strip()
                if not name:
                    continue
                code = _snake_case_upper(name)
                matched_tables = self._resolve_tables(
                    llm_item.tables or [], table_names_lower
                )
                domain = BusinessDomain(
                    name=name,
                    code=code,
                    description=llm_item.description or "",
                    aliases=list(dict.fromkeys([a for a in (llm_item.aliases or []) if a])),
                    tables=matched_tables,
                    confidence=max(0.0, min(1.0, llm_item.confidence or 0.7)),
                    evidence=[f"LLM detected: {name}"],
                )
                domains_by_code[code] = domain

            rule_domains = self._apply_rule_patterns(all_tables, table_comments)
            for rd in rule_domains:
                if rd.code in domains_by_code:
                    existing = domains_by_code[rd.code]
                    existing.tables = list(dict.fromkeys(existing.tables + rd.tables))
                    existing.aliases = list(
                        dict.fromkeys(existing.aliases + rd.aliases)
                    )
                    if rd.evidence:
                        existing.evidence = list(
                            dict.fromkeys((existing.evidence or []) + rd.evidence)
                        )
                    existing.confidence = max(existing.confidence, rd.confidence)
                else:
                    domains_by_code[rd.code] = rd

        except InvalidInputError:
            raise
        except Exception as exc:
            logger.error(
                "Domain detection failed",
                exc_info=exc,
                extra={"error_type": type(exc).__name__},
            )
            raise SemanticAnalysisError(
                detail=f"Failed to detect business domains: {exc!s}",
                extra={"stage": "domain_detection"},
            ) from exc

        domains = list(domains_by_code.values())
        domains.sort(key=lambda d: (-len(d.tables), d.name))

        logger.info(
            "Business domain detection completed",
            extra={"domain_count": len(domains)},
        )
        return domains

    async def _detect_domains_llm(
        self,
        tables: list[TableMetadata],
        hints: list[str],
    ) -> list[_LLMDomainItem]:
        if not tables:
            return []

        table_summaries: list[str] = []
        for t in tables[:50]:
            cols = ", ".join(c.name for c in t.columns[:10])
            comment_suffix = f" - {t.comment}" if t.comment else ""
            table_summaries.append(
                f"- {t.schema_name}.{t.table_name}{comment_suffix} cols: {cols}"
            )

        prompt_text = (
            "You are a data architect. Given a list of database tables, "
            "identify business domains (functional areas). Output canonical names. "
            f"Hints: {', '.join(hints) if hints else 'none'}.\n\n"
            "TABLES:\n" + "\n".join(table_summaries)
        )
        from langchain_core.messages import HumanMessage, SystemMessage

        messages = [
            SystemMessage(
                content="Reply with JSON only, using schema {domains: [{name, description, aliases: [], tables: [], confidence: 0-1}]}."
            ),
            HumanMessage(content=prompt_text),
        ]
        try:
            result = await async_generate_structured(
                messages,
                _LLMDomainList,
                retries=1,
                temperature=0.1,
            )
            return list(result.domains or [])
        except Exception as exc:
            logger.warning(
                "LLM domain detection failed, falling back to rules only",
                extra={"error": str(exc)},
            )
            return []

    def _apply_rule_patterns(
        self,
        tables: list[TableMetadata],
        table_comments: dict[str, str],
    ) -> list[BusinessDomain]:
        results: list[BusinessDomain] = []
        for pattern in DOMAIN_RULE_PATTERNS:
            matched_tables: list[str] = []
            for t in tables:
                full_name = f"{t.schema_name}.{t.table_name}"
                haystack_parts = [
                    t.table_name.lower(),
                    t.schema_name.lower(),
                    full_name.lower(),
                    table_comments.get(full_name, ""),
                ]
                col_names = " ".join(c.name.lower() for c in t.columns)
                haystack_parts.append(col_names)
                haystack = " | ".join(haystack_parts)
                if any(kw.lower() in haystack for kw in pattern["keywords"]):
                    matched_tables.append(full_name)

            if not matched_tables:
                continue

            matched_tables = list(dict.fromkeys(matched_tables))
            confidence = min(1.0, 0.4 + 0.03 * len(matched_tables))

            domain = BusinessDomain(
                name=pattern["name"],
                code=pattern["code"],
                description=pattern["description"],
                aliases=list(dict.fromkeys(pattern["aliases"] + [pattern["name_es"]])),
                tables=matched_tables,
                confidence=confidence,
                evidence=[
                    f"Rule-based match on keywords: {', '.join(pattern['keywords'][:4])}"
                ],
            )
            results.append(domain)

        return results

    def _flatten_tables(self, metadata: DatabaseMetadata) -> list[TableMetadata]:
        tables: list[TableMetadata] = []
        for schema in metadata.schemas:
            for t in schema.tables or []:
                tables.append(t)
            for v in schema.views or []:
                tables.append(v)
        return tables

    def _resolve_tables(
        self,
        raw: list[str],
        lookup: dict[str, str],
    ) -> list[str]:
        resolved: list[str] = []
        for item in raw or []:
            key = (item or "").strip().lower()
            if not key:
                continue
            if key in lookup:
                resolved.append(lookup[key])
                continue
            found = None
            for k, v in lookup.items():
                if key == v.lower():
                    found = v
                    break
                if key in k or k.endswith("." + key):
                    found = v
                    break
            if found:
                resolved.append(found)
        return list(dict.fromkeys(resolved))

    async def persist_domains(
        self,
        domains: list[BusinessDomain],
        **kwargs: Any,
    ) -> bool:
        if not self.semantic_repository:
            logger.warning("persist_domains skipped: no semantic_repository configured")
            return False

        try:
            repo = self.semantic_repository
            if hasattr(repo, "persist_domains"):
                await repo.persist_domains(domains=domains, **kwargs)
            else:
                logger.debug(
                    "semantic_repository has no persist_domains method; stored in memory only"
                )
            logger.info(
                "Persisted domains",
                extra={"domain_count": len(domains)},
            )
            return True
        except Exception as exc:
            logger.error(
                "Failed to persist domains",
                exc_info=exc,
                extra={"error_type": type(exc).__name__},
            )
            raise SemanticAnalysisError(
                detail=f"Failed to persist domains: {exc!s}",
                extra={"stage": "domain_persistence"},
            ) from exc
