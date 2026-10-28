"""Portable column types.

EmbeddingType stores vectors as pgvector `vector(n)` on PostgreSQL and as JSON text
elsewhere (SQLite is used only for local development and the test-suite).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Text
from sqlalchemy.types import TypeDecorator


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """Timezone-aware datetimes that round-trip through SQLite as UTC."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value


class EmbeddingType(TypeDecorator):
    impl = Text
    cache_ok = True

    def __init__(self, dim: int = 384, *args, **kwargs):
        self.dim = dim
        super().__init__(*args, **kwargs)

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            from pgvector.sqlalchemy import Vector

            return dialect.type_descriptor(Vector(self.dim))
        return dialect.type_descriptor(Text())

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        values = [float(x) for x in value]
        if dialect.name == "postgresql":
            return values
        return json.dumps(values)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, str):
            return json.loads(value)
        return [float(x) for x in value]


JSONType = JSON
