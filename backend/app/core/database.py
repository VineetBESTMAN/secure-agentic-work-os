import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

import psycopg
from psycopg.rows import dict_row

from app.core.config import get_settings


class PostgresConnection:
    def __init__(self, database_url: str) -> None:
        self._connection = psycopg.connect(database_url, row_factory=dict_row)

    def __enter__(self) -> "PostgresConnection":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if exc_type is None:
            self._connection.commit()
        else:
            self._connection.rollback()
        self._connection.close()

    def execute(self, sql: str, params: Iterable[Any] = ()):
        cursor = self._connection.cursor()
        cursor.execute(_to_postgres_placeholders(sql), tuple(params))
        return cursor

    def executemany(self, sql: str, params: Iterable[Iterable[Any]]) -> None:
        cursor = self._connection.cursor()
        cursor.executemany(_to_postgres_placeholders(sql), [tuple(row) for row in params])

def get_connection():
    settings = get_settings()
    if is_postgres_database():
        return PostgresConnection(settings.database_url or "")

    database_path = Path(settings.database_path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def is_postgres_database() -> bool:
    database_url = get_settings().database_url
    return bool(database_url and database_url.startswith(("postgresql://", "postgres://")))


def encode_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True)


def decode_json(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    return json.loads(value)


def vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{value:.6f}" for value in values) + "]"


def _to_postgres_placeholders(sql: str) -> str:
    return sql.replace("?", "%s")
