from __future__ import annotations

import base64
import re
import unicodedata
import uuid
from typing import Any, Iterable, Optional, Union
from urllib.parse import urlparse, urlunparse

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings
from app.core.exceptions import SecurityError


_SECRET_KEYWORDS: tuple[str, ...] = (
    "password",
    "passwd",
    "pwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "api-key",
    "private_key",
    "privatekey",
    "access_key",
    "accesskey",
    "authorization",
    "auth",
    "credential",
    "db_url",
    "connection_string",
    "dsn",
    "encryption_key",
)


def generate_request_id() -> str:
    return uuid.uuid4().hex


def mask_password(password: Any) -> str:
    return "***"


def mask_connection_string(url: str) -> str:
    if not isinstance(url, str) or not url:
        return str(url) if url is not None else ""
    try:
        parsed = urlparse(url)
        if not parsed.hostname:
            return _mask_simple(url)
        netloc_parts: list[str] = []
        if parsed.username or parsed.password:
            if parsed.username:
                netloc_parts.append(parsed.username)
            netloc_parts.append(":***@")
        host = parsed.hostname or ""
        netloc_parts.append(host)
        if parsed.port:
            netloc_parts.append(f":{parsed.port}")
        masked = parsed._replace(netloc="".join(netloc_parts))
        return urlunparse(masked)
    except Exception:
        return _mask_simple(url)


def _mask_simple(url: str) -> str:
    return re.sub(
        r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.\-]*://)"
        r"(?P<userinfo>[^\s@/:]+:[^\s@/]+)@",
        r"\g<scheme>***:***@",
        url,
        count=1,
    )


def _is_secret_key(key: Any) -> bool:
    if not isinstance(key, str):
        return False
    normalized = re.sub(r"[\s_\-]", "", key.lower())
    for keyword in _SECRET_KEYWORDS:
        if keyword in normalized:
            return True
    return False


def _looks_like_secret(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    if not stripped:
        return False
    if re.match(r"^(sk|pk|rk|ghp|gho|glpat|xoxb|xoxp|xoxa)-[A-Za-z0-9_\-]{10,}$", stripped):
        return True
    if stripped.startswith("postgres") or stripped.startswith("mysql") or stripped.startswith("mssql"):
        return "://" in stripped and ("@" in stripped or re.search(r":[^/]+@", stripped))
    if "://" in stripped and re.search(r"[^/]:[^/@]+@", stripped):
        return True
    if len(stripped) >= 40 and re.fullmatch(r"[A-Za-z0-9+/=_\-]+", stripped):
        return True
    return False


def sanitize_log_value(value: Any, depth: int = 0, max_depth: int = 4) -> Any:
    if depth > max_depth:
        return mask_password(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "<binary data>"
    if isinstance(value, str):
        if _looks_like_secret(value):
            return mask_connection_string(value) if "://" in value and "@" in value else mask_password(value)
        return value
    if isinstance(value, dict):
        sanitized: dict[Any, Any] = {}
        for k, v in value.items():
            if _is_secret_key(k):
                sanitized[k] = mask_password(v)
            else:
                sanitized[k] = sanitize_log_value(v, depth=depth + 1, max_depth=max_depth)
        return sanitized
    if isinstance(value, (list, tuple, set, frozenset)):
        container_type = type(value)
        sanitized_items = [sanitize_log_value(v, depth=depth + 1, max_depth=max_depth) for v in value]
        try:
            return container_type(sanitized_items)
        except Exception:
            return sanitized_items
    return str(value)


def validate_utf8_text(
    value: Any,
    *,
    max_length: Optional[int] = None,
    allow_control_characters: bool = False,
    field_name: Optional[str] = None,
    strip_null: bool = True,
) -> str:
    if not isinstance(value, (str, bytes, bytearray)):
        raise SecurityError(
            detail="Input must be a text value",
            extra={"field": field_name, "received_type": type(value).__name__},
        )
    if isinstance(value, (bytes, bytearray)):
        try:
            text = bytes(value).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SecurityError(
                detail="Input contains invalid UTF-8 byte sequence",
                extra={"field": field_name},
            ) from exc
    else:
        text = value
    if strip_null:
        text = text.replace("\x00", "")
    if not allow_control_characters:
        cleaned_chars: list[str] = []
        for ch in text:
            cat = unicodedata.category(ch)
            if cat.startswith("C") and ch not in ("\n", "\r", "\t"):
                continue
            cleaned_chars.append(ch)
        text = "".join(cleaned_chars)
    text = unicodedata.normalize("NFC", text)
    if max_length is not None and len(text) > max_length:
        raise SecurityError(
            detail=f"Input exceeds maximum allowed length of {max_length} characters",
            extra={"field": field_name, "length": len(text), "max_length": max_length},
        )
    return text


_fernet_singleton: Optional[Fernet] = None


def _get_fernet() -> Fernet:
    global _fernet_singleton
    if _fernet_singleton is not None:
        return _fernet_singleton
    key_material = settings.METADATA_ENCRYPTION_KEY
    if not key_material:
        raise SecurityError(
            detail="Metadata encryption key is not configured",
            extra={"missing_setting": "METADATA_ENCRYPTION_KEY"},
        )
    try:
        key_bytes = key_material.encode("ascii") if isinstance(key_material, str) else bytes(key_material)
        stripped = key_bytes.strip()
        fernet = Fernet(stripped)
    except Exception as exc:
        try:
            derived = base64.urlsafe_b64encode(base64.urlsafe_b64decode(key_material + "=" * ((4 - len(key_material) % 4) % 4)))
            fernet = Fernet(derived)
        except Exception as fallback_exc:
            raise SecurityError(
                detail="Invalid metadata encryption key material",
            ) from fallback_exc
    _fernet_singleton = fernet
    return _fernet_singleton


def encrypt_secret(
    plaintext: Union[str, bytes, bytearray, memoryview],
    *,
    associated_fields: Optional[Iterable[tuple[str, Any]]] = None,
) -> str:
    if plaintext is None:
        raise SecurityError(detail="Cannot encrypt None as secret")
    if isinstance(plaintext, str):
        payload = plaintext.encode("utf-8")
    elif isinstance(plaintext, (bytes, bytearray, memoryview)):
        payload = bytes(plaintext)
    else:
        raise SecurityError(
            detail="Secret must be a string or bytes-like value",
            extra={"received_type": type(plaintext).__name__},
        )
    if associated_fields is not None:
        delimiter = b"\x00\x01\x00"
        suffix_parts: list[bytes] = []
        for key, val in associated_fields:
            suffix_parts.append(f"{key}={val}".encode("utf-8"))
        if suffix_parts:
            payload = payload + delimiter + delimiter.join(suffix_parts)
    try:
        token = _get_fernet().encrypt(payload)
    except Exception as exc:
        raise SecurityError(
            detail="Failed to encrypt secret value",
        ) from exc
    return token.decode("ascii")


def decrypt_secret(
    ciphertext: Union[str, bytes, bytearray, memoryview],
    *,
    ttl: Optional[int] = None,
) -> str:
    if ciphertext is None:
        raise SecurityError(detail="Cannot decrypt None as secret")
    if isinstance(ciphertext, str):
        token = ciphertext.encode("ascii")
    elif isinstance(ciphertext, (bytes, bytearray, memoryview)):
        token = bytes(ciphertext)
    else:
        raise SecurityError(
            detail="Encrypted secret must be a string or bytes-like value",
            extra={"received_type": type(ciphertext).__name__},
        )
    try:
        payload = _get_fernet().decrypt(token.strip(), ttl=ttl)
    except InvalidToken as exc:
        raise SecurityError(
            detail="Encrypted secret is invalid, corrupted, or decryption key mismatch",
        ) from exc
    except Exception as exc:
        raise SecurityError(
            detail="Failed to decrypt secret value",
        ) from exc
    delimiter = b"\x00\x01\x00"
    if delimiter in payload:
        secret_bytes = payload.split(delimiter, 1)[0]
    else:
        secret_bytes = payload
    try:
        return secret_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise SecurityError(
            detail="Decrypted secret bytes do not form valid UTF-8 text",
        )
