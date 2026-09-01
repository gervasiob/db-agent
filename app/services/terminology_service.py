from __future__ import annotations

import re
from typing import Any, Optional

from app.core.exceptions import InvalidInputError, SemanticAnalysisError
from app.core.logging import get_logger
from app.models.semantic import (
    BusinessConcept,
    BusinessDomain,
    BusinessEntity,
    BusinessMetric,
    DateSemantic,
    Dimension,
    TerminologyEntry,
    TerminologyEntryType,
)

logger = get_logger(__name__)


GLOBAL_ALIAS_MAP: dict[str, list[str]] = {
    "employee": [
        "empleado", "trabajador", "colaborador", "staff",
        "asociado", "personal", "operario", "recurso_humano",
        "rrhh", "trabajadora", "empleada",
    ],
    "employees": [
        "empleados", "trabajadores", "colaboradores", "personas",
        "plantilla", "personal", "equipo", "staff_members",
        "headcount", "recursos_humanos",
    ],
    "customer": [
        "cliente", "consumidor", "usuario", "client",
        "comprador", "adquiriente", "suscriptor",
        "account_holder", "pax", "huésped", "huesped",
    ],
    "customers": [
        "clientes", "consumidores", "usuarios", "clients",
        "compradores", "suscriptores", "base_clientes",
        "clientela", "publico_objetivo",
    ],
    "revenue": [
        "ingresos", "facturacion", "facturación", "ventas",
        "billing", "turnover", "ingreso_total", "volumen_negocio",
        "facturado", "ventas_totales", "ingresos_brutos",
    ],
    "order": [
        "pedido", "orden", "orden_compra", "compra",
        "oc", "solicitud", "purchase_order",
    ],
    "orders": [
        "pedidos", "ordenes", "órdenes", "ordenes_compra",
        "compras", "ocs", "solicitudes",
    ],
    "invoice": [
        "factura", "recibo", "comprobante", "bill",
        "cfdi", "ticket", "boleto", "nota_credito",
    ],
    "invoices": [
        "facturas", "recibos", "comprobantes", "bills",
        "cfdis", "tickets", "boletas",
    ],
    "payment": [
        "pago", "abono", "cobro", "transaccion_pago",
        "liquidacion", "caja", "deposito",
    ],
    "payments": [
        "pagos", "abonos", "cobros", "transacciones",
        "liquidaciones", "cobranzas", "ingresos_caja",
    ],
    "product": [
        "producto", "articulo", "item", "bien",
        "sku", "artículo", "mercaderia", "unidad",
        "referencia", "ref",
    ],
    "products": [
        "productos", "articulos", "artículos", "items",
        "bienes", "sku_list", "catalogo", "catálogo",
        "mercaderias", "mercancías",
    ],
    "active_employee": [
        "empleado_activo", "empleado_vigente", "empleado_corriente",
        "plantilla_activa", "personal_activo", "trabajador_activo",
        "colaborador_vigente", "staff_active", "current_staff",
        "active_headcount", "vigente",
    ],
    "terminated_employee": [
        "empleado_desvinculado", "empleado_cesado", "empleado_inactivo",
        "ex_empleado", "baja_empleado", "empleado_terminado",
        "personal_baja", "former_employee", "ex_staff",
    ],
    "active_customer": [
        "cliente_activo", "cliente_vigente", "cliente_actual",
        "cliente_corriente", "active_client", "current_customer",
        "usuario_activo",
    ],
    "new_customer": [
        "nuevo_cliente", "cliente_nuevo", "altas_clientes",
        "registro_nuevo", "nuevos_registros", "nuevos_usuarios",
        "new_signup", "customer_acquisition",
    ],
    "open_order": [
        "pedido_abierto", "pedido_pendiente", "orden_pendiente",
        "orden_abierta", "compra_pendiente", "outstanding_order",
        "pending_order", "unfulfilled_order",
    ],
    "completed_sale": [
        "venta_completada", "venta_exitosa", "venta_finalizada",
        "venta_pagada", "pedido_completado", "pedido_pagado",
        "orden_pagada", "finalized_sale", "successful_sale",
    ],
    "available_inventory": [
        "inventario_disponible", "stock_disponible", "existencia",
        "unidades_stock", "stock_actual", "inventario_vigente",
        "on_hand_stock", "in_stock", "disponible",
    ],
    "cancelled_invoice": [
        "factura_anulada", "factura_cancelada", "comprobante_anulado",
        "recibo_anulado", "invoice_void", "anulada",
    ],
    "paid_invoice": [
        "factura_pagada", "factura_cobrada", "factura_liquidada",
        "comprobante_pagado", "recibo_cobrado", "invoice_settled",
    ],
    "overdue_invoice": [
        "factura_vencida", "factura_impaga", "factura_pendiente_pago",
        "factura_atrasada", "past_due_invoice", "outstanding_invoice",
        "unpaid_invoice", "vencida",
    ],
    "gross_profit": [
        "ganancia_bruta", "margen_bruto", "utilidad_bruta",
        "beneficio_bruto", "margen_bruto", "gp",
    ],
    "country": [
        "pais", "país", "nacion", "nación", "nation",
        "territorio", "pais_origen", "pais_destino",
    ],
    "region": [
        "region", "región", "provincia", "estado", "zona",
        "departamento_geografico", "territorio_region",
    ],
    "city": [
        "ciudad", "municipio", "pueblo", "localidad",
        "town", "municipio", "poblado",
    ],
    "department": [
        "departamento", "area", "área", "division", "división",
        "unidad", "sector", "gerencia", "direccion", "dirección",
    ],
    "supplier": [
        "proveedor", "proveedores", "vendor", "suministrador",
        "abastecedor", "acreedor",
    ],
    "contract": [
        "contrato", "acuerdo", "convenio", "pacto",
        "agreement", "documento_contrato",
    ],
    "position": [
        "cargo", "puesto", "posicion", "posición", "role_puesto",
        "funcion", "función", "job_title",
    ],
    "role": [
        "rol", "perfil", "papel", "grupo_usuario", "permission_group",
    ],
    "company": [
        "empresa", "organizacion", "organización", "corporacion",
        "corporación", "entidad", "firma", "sociedad",
    ],
    "site": [
        "sede", "ubicacion", "ubicación", "oficina", "sucursal",
        "locacion", "locación", "branch", "office",
    ],
    "warehouse": [
        "almacen", "almacén", "deposito", "depósito", "bodega",
        "centro_distribucion", "planta",
    ],
    "shipment": [
        "envio", "envío", "despacho", "entrega", "guia",
        "guía", "remito", "courier", "envio_mensajeria",
    ],
    "transaction": [
        "transaccion", "transacción", "movimiento", "operacion",
        "operación", "asiento", "record_operacion",
    ],
    "account": [
        "cuenta", "cuenta_contable", "ledger", "mayor",
        "libro_mayor", "cuenta_bancaria",
    ],
    "category": [
        "categoria", "categoría", "familia", "grupo", "rubro",
        "linea", "línea", "segmento", "clasificacion", "clasificación",
    ],
    "status": [
        "estado", "estatus", "situacion", "situación", "condicion",
        "condición", "state",
    ],
    "type": [
        "tipo", "clase", "modalidad", "especie", "clase_tipo",
        "kind", "mode",
    ],
    "address": [
        "direccion", "dirección", "domicilio", "calle", "ubicacion_fisica",
        "location", "residencia", "destino_envio",
    ],
    "manager": [
        "gerente", "jefe", "supervisor", "lider", "líder",
        "director", "coordinador", "boss", "responsable",
    ],
    "created_at": [
        "fecha_creacion", "fecha_alta", "fecha_creado",
        "creation_date", "date_created", "inserted_at",
        "dt_creacion", "fec_creacion",
    ],
    "updated_at": [
        "fecha_modificacion", "fecha_actualizacion", "ultima_modificacion",
        "modified_at", "last_updated", "dt_modificacion",
    ],
    "deleted_at": [
        "fecha_baja", "fecha_eliminacion", "fecha_desactivacion",
        "deleted_at", "soft_deleted", "dt_baja",
    ],
    "order_date": [
        "fecha_pedido", "fecha_orden", "fecha_compra",
        "purchase_date", "placed_at", "fecha_venta",
    ],
    "invoice_date": [
        "fecha_factura", "fecha_emision", "fecha_facturacion",
        "billing_date", "issue_date", "fec_factura",
    ],
    "payment_date": [
        "fecha_pago", "fecha_cobro", "fecha_pagado",
        "paid_at", "collection_date", "fec_pago",
    ],
    "hire_date": [
        "fecha_contratacion", "fecha_ingreso", "fecha_incorporacion",
        "hire_date", "date_joined", "fec_ingreso",
    ],
    "termination_date": [
        "fecha_desvinculacion", "fecha_salida", "fecha_cese",
        "termination_date", "end_date", "fecha_baja_empleado",
    ],
    "birth_date": [
        "fecha_nacimiento", "fecha_nac", "dob", "nacimiento",
        "birth_date", "cumpleanos", "cumpleaños",
    ],
    "hr": ["rrhh", "recursos_humanos", "human_resources", "personal_hr"],
    "sales": ["ventas", "sales_department", "area_comercial", "comercial"],
    "finance": ["finanzas", "financial", "contabilidad", "tesoreria", "financial_dept"],
    "inventory": ["inventario", "stock", "almacen_stock", "inventario_fisico"],
    "logistics": ["logistica", "logística", "envios", "distribucion", "cadena_suministro"],
    "billing": ["facturacion", "cobranzas", "cuentas_cobrar", "billing_dept"],
    "audit": ["auditoria", "auditoría", "logs", "bitacora", "bitácora", "registro_actividad"],
    "configuration": ["configuracion", "configuración", "parametros", "parámetros", "settings", "config"],
}


def _canonical(text: str) -> str:
    t = (text or "").strip().lower()
    t = re.sub(r"[^a-z0-9áéíóúñ]+", "_", t)
    t = t.strip("_")
    return t


def _title_case(text: str) -> str:
    return " ".join(w.capitalize() for w in (text or "").replace("_", " ").split())


class TerminologyService:
    def __init__(
        self,
        semantic_repository: Any = None,
    ) -> None:
        self.semantic_repository = semantic_repository
        logger.info(
            "TerminologyService initialized",
            extra={"has_repository": semantic_repository is not None},
        )

    async def build_terminology(
        self,
        domains: list[BusinessDomain] | None = None,
        entities: list[BusinessEntity] | None = None,
        concepts: list[BusinessConcept] | None = None,
        metrics: list[BusinessMetric] | None = None,
        dimensions: list[Dimension] | None = None,
        date_semantics: list[DateSemantic] | None = None,
    ) -> list[TerminologyEntry]:
        entries: list[TerminologyEntry] = []
        seen: set[tuple[str, str, Optional[str]]] = set()

        logger.info(
            "Building terminology glossary",
            extra={
                "domains": len(domains or []),
                "entities": len(entities or []),
                "concepts": len(concepts or []),
                "metrics": len(metrics or []),
                "dimensions": len(dimensions or []),
                "date_semantics": len(date_semantics or []),
            },
        )

        try:
            for d in domains or []:
                self._add_entry(
                    entries,
                    seen,
                    term=d.name,
                    canonical_form=_canonical(d.name),
                    aliases=self._gather_aliases(d.name, d.aliases, d.code),
                    type=TerminologyEntryType.DOMAIN,
                    target_code=d.code,
                    confidence=d.confidence,
                )
                for alias in d.aliases or []:
                    self._add_entry(
                        entries,
                        seen,
                        term=alias,
                        canonical_form=_canonical(d.name),
                        aliases=self._gather_aliases(d.name, d.aliases, d.code, exclude=alias),
                        type=TerminologyEntryType.DOMAIN,
                        target_code=d.code,
                        confidence=max(0.0, d.confidence - 0.1),
                    )

            for e in entities or []:
                self._add_entry(
                    entries,
                    seen,
                    term=e.name,
                    canonical_form=_canonical(e.name),
                    aliases=self._gather_aliases(e.name, e.aliases, e.code),
                    type=TerminologyEntryType.ENTITY,
                    target_code=e.code,
                    confidence=e.confidence,
                )
                for alias in e.aliases or []:
                    self._add_entry(
                        entries,
                        seen,
                        term=alias,
                        canonical_form=_canonical(e.name),
                        aliases=self._gather_aliases(e.name, e.aliases, e.code, exclude=alias),
                        type=TerminologyEntryType.ENTITY,
                        target_code=e.code,
                        confidence=max(0.0, e.confidence - 0.1),
                    )

            for c in concepts or []:
                self._add_entry(
                    entries,
                    seen,
                    term=c.name,
                    canonical_form=_canonical(c.name),
                    aliases=self._gather_aliases(c.name, c.aliases, c.code),
                    type=TerminologyEntryType.CONCEPT,
                    target_code=c.code,
                    confidence=c.confidence,
                )
                for alias in c.aliases or []:
                    self._add_entry(
                        entries,
                        seen,
                        term=alias,
                        canonical_form=_canonical(c.name),
                        aliases=self._gather_aliases(c.name, c.aliases, c.code, exclude=alias),
                        type=TerminologyEntryType.CONCEPT,
                        target_code=c.code,
                        confidence=max(0.0, c.confidence - 0.1),
                    )

            for m in metrics or []:
                self._add_entry(
                    entries,
                    seen,
                    term=m.name,
                    canonical_form=_canonical(m.name),
                    aliases=self._gather_aliases(m.name, m.aliases, m.code),
                    type=TerminologyEntryType.METRIC,
                    target_code=m.code,
                    confidence=m.confidence,
                )
                for alias in m.aliases or []:
                    self._add_entry(
                        entries,
                        seen,
                        term=alias,
                        canonical_form=_canonical(m.name),
                        aliases=self._gather_aliases(m.name, m.aliases, m.code, exclude=alias),
                        type=TerminologyEntryType.METRIC,
                        target_code=m.code,
                        confidence=max(0.0, m.confidence - 0.1),
                    )

            for dim in dimensions or []:
                self._add_entry(
                    entries,
                    seen,
                    term=dim.name,
                    canonical_form=_canonical(dim.name),
                    aliases=self._gather_aliases(dim.name, dim.aliases, dim.code),
                    type=TerminologyEntryType.DIMENSION,
                    target_code=dim.code,
                    confidence=dim.confidence,
                )

            global_added = 0
            for canonical_key, alias_list in GLOBAL_ALIAS_MAP.items():
                title_form = _title_case(canonical_key)
                canonical_norm = _canonical(title_form)
                related_entries = [
                    e
                    for e in entries
                    if _canonical(e.term) == canonical_norm
                    or _canonical(e.canonical_form) == canonical_norm
                ]
                if not related_entries:
                    continue
                base_type = related_entries[0].type
                base_target = related_entries[0].target_code
                for alias in alias_list:
                    alias_title = _title_case(alias)
                    self._add_entry(
                        entries,
                        seen,
                        term=alias_title,
                        canonical_form=canonical_norm,
                        aliases=[
                            a
                            for a in [title_form]
                            + [_title_case(x) for x in alias_list if x != alias]
                            if a.lower() != alias_title.lower()
                        ],
                        type=base_type,
                        target_code=base_target,
                        confidence=0.85,
                    )
                    global_added += 1

            logger.debug(
                "Added global alias entries",
                extra={"global_alias_entries": global_added},
            )

            for ds in date_semantics or []:
                ds_name = f"{ds.table_name}.{ds.column_name}"
                self._add_entry(
                    entries,
                    seen,
                    term=ds_name,
                    canonical_form=_canonical(f"{ds.semantic_role.value} {ds.column_name}"),
                    aliases=list(ds.aliases or []),
                    type=TerminologyEntryType.COLUMN,
                    target_code=f"{ds.table_name}.{ds.column_name}",
                    confidence=ds.confidence,
                )

            entries.sort(key=lambda e: (-e.confidence, e.type.value, e.term))

        except Exception as exc:
            logger.error(
                "Terminology build failed",
                exc_info=exc,
                extra={"error_type": type(exc).__name__},
            )
            raise SemanticAnalysisError(
                detail=f"Failed to build terminology: {exc!s}",
                extra={"stage": "terminology_build"},
            ) from exc

        logger.info(
            "Terminology build completed",
            extra={"entry_count": len(entries)},
        )
        return entries

    def _add_entry(
        self,
        entries: list[TerminologyEntry],
        seen: set[tuple[str, str, Optional[str]]],
        *,
        term: str,
        canonical_form: str,
        aliases: list[str],
        type: TerminologyEntryType,
        target_code: Optional[str],
        confidence: float,
    ) -> None:
        term_norm = _canonical(term)
        if not term_norm:
            return
        key = (term_norm, type.value, target_code)
        if key in seen:
            return
        seen.add(key)
        clean_aliases = list(dict.fromkeys([a for a in aliases if _canonical(a) and _canonical(a) != term_norm]))
        entry = TerminologyEntry(
            term=term,
            canonical_form=canonical_form,
            aliases=clean_aliases,
            type=type,
            target_code=target_code,
            confidence=max(0.0, min(1.0, confidence)),
        )
        entries.append(entry)

    def _gather_aliases(
        self,
        name: str,
        existing_aliases: list[str] | None,
        code: Optional[str],
        *,
        exclude: Optional[str] = None,
    ) -> list[str]:
        out: list[str] = []
        name_norm = _canonical(name)
        exclude_norm = _canonical(exclude) if exclude else None

        if existing_aliases:
            for a in existing_aliases:
                a_norm = _canonical(a)
                if a_norm and a_norm != name_norm and a_norm != exclude_norm:
                    out.append(a)

        for canonical_key, alias_list in GLOBAL_ALIAS_MAP.items():
            if _canonical(canonical_key) == name_norm or _canonical(_title_case(canonical_key)) == name_norm:
                for alias in alias_list:
                    alias_title = _title_case(alias)
                    a_norm = _canonical(alias_title)
                    if a_norm and a_norm != name_norm and a_norm != exclude_norm:
                        out.append(alias_title)

        if code:
            code_title = code.replace("_", " ").title()
            code_norm = _canonical(code_title)
            if code_norm and code_norm != name_norm and code_norm != exclude_norm:
                out.append(code_title)

        return list(dict.fromkeys(out))

    async def persist_terminology(
        self,
        terminology: list[TerminologyEntry],
        **kwargs: Any,
    ) -> bool:
        if not self.semantic_repository:
            logger.warning("persist_terminology skipped: no semantic_repository configured")
            return False
        try:
            repo = self.semantic_repository
            if hasattr(repo, "persist_terminology"):
                await repo.persist_terminology(terminology=terminology, **kwargs)
            else:
                logger.debug(
                    "semantic_repository has no persist_terminology method; stored in memory only"
                )
            logger.info(
                "Persisted terminology",
                extra={"entry_count": len(terminology)},
            )
            return True
        except Exception as exc:
            logger.error(
                "Failed to persist terminology",
                exc_info=exc,
                extra={"error_type": type(exc).__name__},
            )
            raise SemanticAnalysisError(
                detail=f"Failed to persist terminology: {exc!s}",
                extra={"stage": "terminology_persistence"},
            ) from exc
