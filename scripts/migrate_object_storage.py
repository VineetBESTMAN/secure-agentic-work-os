"""Copy legacy local document objects to configured S3 storage without deleting sources."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import get_settings  # noqa: E402
from app.core.database import get_connection  # noqa: E402
from app.services.object_storage import object_storage_service  # noqa: E402


def migrate(*, execute: bool) -> tuple[int, int]:
    settings = get_settings()
    if settings.object_storage_backend != "s3":
        raise RuntimeError("Configure APP_OBJECT_STORAGE_BACKEND=s3 before migration.")
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT document_id, organization_id, filename, storage_backend, storage_key
            FROM documents
            WHERE storage_backend != 's3' OR storage_key IS NULL
            ORDER BY created_at ASC
            """
        ).fetchall()

    migrated = 0
    for row in rows:
        source_key = row["storage_key"] or object_storage_service.legacy_document_key(
            document_id=row["document_id"], filename=row["filename"]
        )
        target_key = object_storage_service.document_key(
            organization_id=row["organization_id"],
            document_id=row["document_id"],
            filename=row["filename"],
        )
        print(f"{row['document_id']}: {row['storage_backend']} -> s3:{target_key}")
        if not execute:
            continue
        data = object_storage_service.get(source_key, backend=row["storage_backend"])
        object_storage_service.put(target_key, data, filename=row["filename"])
        copied = object_storage_service.get(target_key, backend="s3")
        if hashlib.sha256(copied).digest() != hashlib.sha256(data).digest():
            object_storage_service.delete(target_key, backend="s3")
            raise RuntimeError(f"Object verification failed for {row['document_id']}.")
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE documents SET storage_backend = 's3', storage_key = ?
                WHERE document_id = ? AND organization_id = ?
                """,
                (target_key, row["document_id"], row["organization_id"]),
            )
        migrated += 1
    return len(rows), migrated


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Copy legacy local objects to S3. Source files are always retained for rollback."
        )
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Perform copies and metadata updates; without this flag only print the plan.",
    )
    args = parser.parse_args()
    total, migrated = migrate(execute=args.execute)
    mode = "migrated" if args.execute else "planned"
    print(f"{mode} {migrated if args.execute else total} of {total} documents")


if __name__ == "__main__":
    main()
