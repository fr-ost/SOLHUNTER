"""Helpers to turn ORM rows into JSON-safe dictionaries."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import inspect


def orm_to_dict(obj: Any, exclude: set[str] | None = None) -> dict[str, Any]:
    if obj is None:
        return {}
    exclude = exclude or set()
    mapper = inspect(obj).mapper
    out: dict[str, Any] = {}
    for column in mapper.column_attrs:
        if column.key in exclude:
            continue
        value = getattr(obj, column.key)
        if isinstance(value, datetime):
            value = value.isoformat()
        elif isinstance(value, bytes | bytearray):
            value = f"<{len(value)} bytes>"
        out[column.key] = value
    return out


def rows_to_dicts(rows: list[Any], exclude: set[str] | None = None) -> list[dict[str, Any]]:
    return [orm_to_dict(r, exclude) for r in rows]
