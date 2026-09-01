from __future__ import annotations

import re
import uuid as _uuid_module
from typing import Any, Optional

try:
    import phonenumbers
except ImportError:  # pragma: no cover
    phonenumbers = None

from app.core.logging import get_logger
from app.models.database import ColumnMetadata, DatabaseMetadata, TableMetadata
from app.models.semantic import PIILevel

logger = get_logger(__name__)


_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"
)

_INTERNATIONAL_PHONE_RE = re.compile(
    r"(?:(?<!\w)(?:\+\d{1,3}[-.\s]?)?(?:\(\d{1,4}\)|\d{1,4})[-.\s]?\d{1,4}[-.\s]?\d{1,9}(?!\w))"
)

_DNI_AR_RE = re.compile(
    r"\b\d{1,2}[.\s]?\d{3}[.\s]?\d{3}\b"
)

_CUIT_CUIL_AR_RE = re.compile(
    r"\b\d{2}[- ]?\d{4,8}[- ]?\d\b"
)

_CPF_BR_RE = re.compile(
    r"\b\d{3}[\.\s]?\d{3}[\.\s]?\d{3}[- ]?\d{2}\b"
)

_CNPJ_BR_RE = re.compile(
    r"\b\d{2}[\.\s]?\d{3}[\.\s]?\d{3}[\/\s]?\d{4}[- ]?\d{2}\b"
)

_CREDIT_CARD_RE = re.compile(
    r"\b(?:\d{4}[\s\-]?){3}\d{4}\b"
)

_PASSWORD_RE = re.compile(
    r"\b(?:p(?:ass)?w(?:or)?d|pwd|passwd|secret(?:_?key)?|private_key|api_?key|apikey|authorization|bearer)\s*[:=]\s*['\"]?[\w\-+=/@#$%^&*()!]{6,}['\"]?\b",
    re.IGNORECASE,
)

_TOKEN_RE = re.compile(
    r"\b(?:sk|pk|rk|ghp|gho|glpat|xoxb|xoxp|xoxa|xoxs|eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+|Bearer\s+[A-Za-z0-9_\-\.]{8,})\b"
)

_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}\b-[0-9a-fA-F]{4}\b-[0-9a-fA-F]{4}\b-[0-9a-fA-F]{4}\b-[0-9a-fA-F]{12}\b"
)

_ADDRESS_KEYWORDS = (
    "address",
    "direccion",
    "street",
    "calle",
    "avenue",
    "avenida",
    "av.",
    "city",
    "ciudad",
    "town",
    "pueblo",
    "state",
    "provincia",
    "province",
    "country",
    "pais",
    "zip",
    "postal",
    "codigo_postal",
    "cp ",
    "location",
    "ubicacion",
    "district",
    "distrito",
    "department",
    "departamento",
    "neighborhood",
    "barrio",
    "borough",
    "latitude",
    "latitud",
    "longitude",
    "longitud",
    "calle ",
    "avenida ",
    "number",
    "numero",
    "nro",
    "piso",
    "depto",
    "floor",
    "apt",
    "suite",
    "oficina",
)


def _luhn_check(number_str: str) -> bool:
    digits = re.sub(r"\D", "", number_str)
    if len(digits) < 13 or len(digits) > 19:
        return False
    total = 0
    reverse = digits[::-1]
    for idx, ch in enumerate(reverse):
        try:
            d = int(ch)
        except ValueError:
            return False
        if idx % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _looks_like_uuid(value: str) -> bool:
    if _UUID_RE.fullmatch(value):
        return True
    try:
        _uuid_module.UUID(value)
        return True
    except Exception:
        return False


class DataSanitizationService:
    def __init__(self, *, default_pii_level: PIILevel = PIILevel.MEDIUM) -> None:
        self.default_pii_level = default_pii_level

    def _classify_pii_for_redaction(self, level: PIILevel) -> set[str]:
        if level == PIILevel.NONE:
            return set()
        if level == PIILevel.LOW:
            return {"SECRET", "DOCUMENT", "CARD"}
        if level == PIILevel.MEDIUM:
            return {"SECRET", "DOCUMENT", "CARD", "EMAIL", "PHONE"}
        if level == PIILevel.HIGH:
            return {"SECRET", "DOCUMENT", "CARD", "EMAIL", "PHONE", "ADDRESS"}
        return {"SECRET", "DOCUMENT", "CARD", "EMAIL", "PHONE", "ADDRESS"}

    def _redact_emails(self, text: str) -> tuple[str, bool]:
        changed = False

        def _repl(match: re.Match) -> str:
            nonlocal changed
            changed = True
            return "[EMAIL]"

        return _EMAIL_RE.sub(_repl, text), changed

    def _redact_credit_cards(self, text: str) -> tuple[str, bool]:
        changed = False

        def _repl(match: re.Match) -> str:
            nonlocal changed
            if _luhn_check(match.group(0)):
                changed = True
                return "[CARD]"
            return match.group(0)

        return _CREDIT_CARD_RE.sub(_repl, text), changed

    def _redact_documents(self, text: str) -> tuple[str, bool]:
        changed = False

        def _dn_repl(match: re.Match) -> str:
            nonlocal changed
            changed = True
            return "[DOCUMENT]"

        for pattern in (_DNI_AR_RE, _CUIT_CUIL_AR_RE, _CPF_BR_RE, _CNPJ_BR_RE):
            new_text, doc_changed = pattern.subn(_dn_repl, text)
            if doc_changed:
                text = new_text
                changed = True
        return text, changed

    def _redact_phones(self, text: str) -> tuple[str, bool]:
        changed = False
        if phonenumbers is not None:
            try:
                matches = list(
                    phonenumbers.PhoneNumberMatcher(text, "AR")
                )
                if not matches:
                    matches = list(phonenumbers.PhoneNumberMatcher(text, None))
                if matches:
                    processed = text
                    matches_sorted = sorted(
                        matches,
                        key=lambda m: (m.start, - (m.end - m.start)),
                    )
                    offset = 0
                    for m in matches_sorted:
                        start = m.start + offset
                        end = m.end + offset
                        if start < 0 or end > len(processed):
                            continue
                        processed = processed[:start] + "[PHONE]" + processed[end:]
                        offset += len("[PHONE]") - (m.end - m.start)
                        changed = True
                    if changed:
                        return processed, changed
            except Exception:
                pass

        new_text, re_changed = _INTERNATIONAL_PHONE_RE.subn(lambda m: "[PHONE]", text)
        if re_changed:
            return new_text, True
        return text, changed

    def _redact_secrets(self, text: str) -> tuple[str, bool]:
        changed = False

        def _pw_repl(match: re.Match) -> str:
            nonlocal changed
            changed = True
            prefix_match = re.match(r"(.*?[:=]\s*['\"]?)", match.group(0))
            prefix = prefix_match.group(1) if prefix_match else ""
            return prefix + "[SECRET]"

        new_text, pw_changed = _PASSWORD_RE.subn(_pw_repl, text)
        if pw_changed:
            changed = True
            text = new_text

        def _token_repl(match: re.Match) -> str:
            nonlocal changed
            changed = True
            return "[SECRET]"

        new_text, t_changed = _TOKEN_RE.subn(_token_repl, text)
        if t_changed:
            changed = True
            text = new_text

        return text, changed

    def _redact_addresses(self, text: str) -> tuple[str, bool]:
        lower = text.lower()
        for kw in _ADDRESS_KEYWORDS:
            idx = lower.find(kw)
            if idx >= 0:
                return "[ADDRESS]", True
        return text, False

    def sanitize_value(self, value: Any, pii_level: PIILevel = PIILevel.MEDIUM) -> Any:
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        if not value:
            return value

        redact_categories = self._classify_pii_for_redaction(pii_level)
        if not redact_categories:
            return value

        processed = value

        if "SECRET" in redact_categories:
            processed, _ = self._redact_secrets(processed)

        if "CARD" in redact_categories:
            processed, _ = self._redact_credit_cards(processed)

        if "DOCUMENT" in redact_categories:
            processed, _ = self._redact_documents(processed)

        if "EMAIL" in redact_categories:
            processed, _ = self._redact_emails(processed)

        if "PHONE" in redact_categories:
            processed, _ = self._redact_phones(processed)

        if "ADDRESS" in redact_categories:
            processed, _ = self._redact_addresses(processed)

        return processed

    def sanitize_rows(
        self,
        rows: list[dict[str, Any]],
        *,
        pii_by_column: Optional[dict[str, PIILevel]] = None,
        default_pii: PIILevel = PIILevel.MEDIUM,
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for row in rows:
            sanitized_row: dict[str, Any] = {}
            for col_name, value in row.items():
                level = (
                    pii_by_column.get(col_name, default_pii)
                    if pii_by_column
                    else default_pii
                )
                sanitized_row[col_name] = self.sanitize_value(value, level)
            result.append(sanitized_row)
        return result

    def classify_column_pii(
        self,
        column: ColumnMetadata,
        samples: Optional[list[Any]] = None,
    ) -> PIILevel:
        name = column.name.lower()
        heur = column.heuristics
        sample_list = list(samples) if samples else []

        level_scores: dict[PIILevel, int] = {level: 0 for level in PIILevel}

        if heur:
            if heur.is_email or heur.is_phone:
                level_scores[PIILevel.HIGH] += 3
            if heur.is_address:
                level_scores[PIILevel.MEDIUM] += 2
            if heur.is_name:
                level_scores[PIILevel.MEDIUM] += 2
            if heur.is_id:
                level_scores[PIILevel.NONE] += 1
            if heur.is_date or heur.is_timestamp or heur.is_created_at or heur.is_updated_at or heur.is_status or heur.is_audit:
                level_scores[PIILevel.NONE] += 1
            if heur.is_monetary:
                level_scores[PIILevel.LOW] += 1

        if name in {"email", "correo", "correo_electronico", "mail"}:
            level_scores[PIILevel.HIGH] += 5
        if any(w in name for w in ("phone", "telefono", "tel", "mobile", "celular", "whatsapp", "contact_number")):
            level_scores[PIILevel.HIGH] += 5
        if any(w in name for w in ("document", "dni", "cuil", "cuit", "cpf", "cnpj", "cedula", "rut")):
            level_scores[PIILevel.HIGH] += 5
        if any(w in name for w in ("credit_card", "tarjeta", "card_number", "cc_number", "pan")):
            level_scores[PIILevel.REDACTED] += 10
        if any(w in name for w in ("password", "passwd", "pwd", "secret", "token", "api_key", "apikey", "private_key", "ssn")):
            level_scores[PIILevel.REDACTED] += 10
        if any(w in name for w in ("address", "direccion", "street", "calle", "city", "ciudad", "state", "provincia", "zip", "postal", "location", "ubicacion")):
            level_scores[PIILevel.HIGH] += 3
        if any(w in name for w in ("first_name", "last_name", "nombre", "apellido", "full_name")):
            level_scores[PIILevel.MEDIUM] += 3
        if any(w in name for w in ("amount", "price", "cost", "total", "salary", "monto", "precio", "costo", "sueldo")):
            level_scores[PIILevel.LOW] += 1
        if name.endswith("_id") or name == "id" or name in {"status", "estado", "created_at", "updated_at", "deleted_at"}:
            level_scores[PIILevel.NONE] += 3

        sample_scores: dict[PIILevel, int] = {level: 0 for level in PIILevel}
        for raw_sample in sample_list[:20]:
            sample = raw_sample
            if sample is None:
                continue
            if not isinstance(sample, str):
                sample = str(sample)
            s = sample.strip()
            if not s:
                continue
            if _EMAIL_RE.search(s):
                sample_scores[PIILevel.HIGH] += 3
            if _CREDIT_CARD_RE.search(s) and _luhn_check(_CREDIT_CARD_RE.search(s).group(0)):
                sample_scores[PIILevel.REDACTED] += 10
            if _TOKEN_RE.search(s):
                sample_scores[PIILevel.REDACTED] += 5
            if _looks_like_uuid(s):
                sample_scores[PIILevel.LOW] += 1
            if phonenumbers is not None:
                try:
                    for region in ("AR", "BR", "US", None):
                        try:
                            parsed = phonenumbers.parse(s, region)
                            if phonenumbers.is_valid_number(parsed):
                                sample_scores[PIILevel.HIGH] += 3
                                break
                        except Exception:
                            continue
                except Exception:
                    pass
            for pattern in (_DNI_AR_RE, _CUIT_CUIL_AR_RE, _CPF_BR_RE, _CNPJ_BR_RE):
                if pattern.search(s):
                    sample_scores[PIILevel.HIGH] += 3
                    break
            lower_s = s.lower()
            if any(kw in lower_s for kw in _ADDRESS_KEYWORDS):
                sample_scores[PIILevel.HIGH] += 2

        combined: dict[PIILevel, int] = {level: level_scores[level] + sample_scores[level] for level in PIILevel}
        order = [
            PIILevel.REDACTED,
            PIILevel.HIGH,
            PIILevel.MEDIUM,
            PIILevel.LOW,
            PIILevel.NONE,
        ]
        best_level = PIILevel.NONE
        best_score = 0
        for level in order:
            if combined[level] > best_score:
                best_level = level
                best_score = combined[level]

        if best_score == 0:
            return PIILevel.NONE
        return best_level

    def sanitize_table_samples(
        self,
        metadata: DatabaseMetadata,
        *,
        default_pii: PIILevel = PIILevel.MEDIUM,
    ) -> DatabaseMetadata:
        for schema in metadata.schemas:
            for collection in (schema.tables, schema.views):
                for table in collection:
                    if not table.sample_rows:
                        continue
                    pii_by_col: dict[str, PIILevel] = {}
                    for col in table.columns:
                        col_samples: list[Any] = []
                        if table.sample_rows:
                            for row in table.sample_rows:
                                if col.name in row:
                                    col_samples.append(row[col.name])
                        pii_by_col[col.name] = self.classify_column_pii(col, col_samples)
                    table.sample_rows = self.sanitize_rows(
                        table.sample_rows,
                        pii_by_column=pii_by_col,
                        default_pii=default_pii,
                    )
        return metadata

    def sanitize_table_rows(
        self,
        table: TableMetadata,
        rows: list[dict[str, Any]],
        *,
        default_pii: PIILevel = PIILevel.MEDIUM,
    ) -> list[dict[str, Any]]:
        pii_by_col: dict[str, PIILevel] = {}
        for col in table.columns:
            col_samples: list[Any] = []
            for row in rows:
                if col.name in row:
                    col_samples.append(row[col.name])
            pii_by_col[col.name] = self.classify_column_pii(col, col_samples)
        return self.sanitize_rows(rows, pii_by_column=pii_by_col, default_pii=default_pii)


__all__ = ["DataSanitizationService"]
