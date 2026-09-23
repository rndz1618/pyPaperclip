from __future__ import annotations

import hashlib
import json
import re
import secrets
from typing import Any


ROLES = {"owner", "admin", "operator", "viewer"}


def hash_key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_api_key() -> str:
    return "ppk_" + secrets.token_urlsafe(32)


def redact(value: Any) -> Any:
    """Remove common credential values recursively before audit/log persistence."""
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if re.search(r"(api[_-]?key|authorization|token|secret|password|cookie)", str(key), re.I):
                result[key] = "[REDACTED]"
            else:
                result[key] = redact(item)
        return result
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, (dict, list)):
                return json.dumps(redact(parsed), separators=(",", ":"))
        except json.JSONDecodeError:
            pass
        return re.sub(r"(?i)(bearer\s+|sk-[A-Za-z0-9_-]{12,})[A-Za-z0-9._~+/=-]*", "[REDACTED]", value)
    return value


def can(role: str, action: str) -> bool:
    permissions = {
        "owner": {"read", "write", "approve", "lifecycle", "admin"},
        "admin": {"read", "write", "approve", "lifecycle", "admin"},
        "operator": {"read", "write", "lifecycle"},
        "viewer": {"read"},
    }
    return action in permissions.get(role, set())
