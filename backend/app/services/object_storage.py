from __future__ import annotations

from datetime import datetime, timezone
import mimetypes
from pathlib import Path, PurePosixPath
from typing import Any

import boto3
from botocore.config import Config

from app.core.config import get_settings


class ObjectStorageError(RuntimeError):
    pass


class ObjectStorageService:
    def document_key(
        self, *, organization_id: str, document_id: str, filename: str
    ) -> str:
        safe_org = self._segment(organization_id)
        safe_document = self._segment(document_id)
        safe_filename = Path(filename).name or "uploaded-document.txt"
        return self._validate_key(
            f"documents/{safe_org}/{safe_document}/{safe_filename}"
        )

    def legacy_document_key(self, *, document_id: str, filename: str) -> str:
        return self._validate_key(f"{document_id}_{Path(filename).name}")

    def put(self, key: str, data: bytes, *, filename: str | None = None) -> str:
        logical_key = self._validate_key(key)
        settings = get_settings()
        if settings.object_storage_backend == "local":
            path = self._local_path(logical_key)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            return logical_key

        content_type = mimetypes.guess_type(filename or logical_key)[0]
        kwargs: dict[str, Any] = {
            "Bucket": self._bucket(),
            "Key": self._remote_key(logical_key),
            "Body": data,
        }
        if content_type:
            kwargs["ContentType"] = content_type
        if settings.object_storage_sse:
            kwargs["ServerSideEncryption"] = settings.object_storage_sse
        if settings.object_storage_sse == "aws:kms":
            kwargs["SSEKMSKeyId"] = settings.object_storage_kms_key_id
        try:
            self._client().put_object(**kwargs)
        except Exception as exc:
            raise ObjectStorageError("Could not persist the object.") from exc
        return logical_key

    def get(self, key: str, *, backend: str | None = None) -> bytes:
        logical_key = self._validate_key(key)
        settings = get_settings()
        selected_backend = backend or settings.object_storage_backend
        if selected_backend == "local":
            path = self._local_path(logical_key)
            try:
                return path.read_bytes()
            except OSError as exc:
                raise ObjectStorageError("The stored object is unavailable.") from exc
        try:
            response = self._client().get_object(
                Bucket=self._bucket(), Key=self._remote_key(logical_key)
            )
            return response["Body"].read()
        except Exception as exc:
            raise ObjectStorageError("The stored object is unavailable.") from exc

    def delete(self, key: str, *, backend: str | None = None) -> None:
        logical_key = self._validate_key(key)
        settings = get_settings()
        selected_backend = backend or settings.object_storage_backend
        if selected_backend == "local":
            self._local_path(logical_key).unlink(missing_ok=True)
            return
        try:
            self._client().delete_object(
                Bucket=self._bucket(), Key=self._remote_key(logical_key)
            )
        except Exception as exc:
            raise ObjectStorageError("Could not delete the stored object.") from exc

    def exists(self, key: str, *, backend: str | None = None) -> bool:
        logical_key = self._validate_key(key)
        settings = get_settings()
        selected_backend = backend or settings.object_storage_backend
        if selected_backend == "local":
            return self._local_path(logical_key).is_file()
        try:
            self._client().head_object(
                Bucket=self._bucket(), Key=self._remote_key(logical_key)
            )
            return True
        except Exception:
            return False

    def health(self) -> tuple[bool, str]:
        settings = get_settings()
        if settings.object_storage_backend == "local":
            try:
                root = Path(settings.upload_dir)
                root.mkdir(parents=True, exist_ok=True)
                probe = root / ".storage-health"
                probe.write_bytes(b"ok")
                probe.unlink(missing_ok=True)
                return True, "local storage is writable"
            except OSError:
                return False, "local storage is not writable"
        try:
            self._client().head_bucket(Bucket=self._bucket())
            return True, "object storage bucket is reachable"
        except Exception:
            return False, "object storage bucket is unavailable"

    def latest_backup_at(self) -> datetime | None:
        settings = get_settings()
        if settings.object_storage_backend != "s3":
            return None
        logical_prefix = self._validate_key(settings.backup_status_prefix)
        candidates: list[object] = []
        continuation_token: str | None = None
        try:
            client = self._client()
            while True:
                kwargs: dict[str, Any] = {
                    "Bucket": self._bucket(),
                    "Prefix": self._remote_key(logical_prefix).rstrip("/") + "/",
                }
                if continuation_token:
                    kwargs["ContinuationToken"] = continuation_token
                response = client.list_objects_v2(**kwargs)
                candidates.extend(
                    item.get("LastModified")
                    for item in response.get("Contents", [])
                    if str(item.get("Key", "")).endswith(".dump")
                )
                if not response.get("IsTruncated"):
                    break
                continuation_token = response.get("NextContinuationToken")
                if not continuation_token:
                    break
        except Exception as exc:
            raise ObjectStorageError("Could not inspect backup objects.") from exc
        timestamps = [item for item in candidates if isinstance(item, datetime)]
        if not timestamps:
            return None
        latest = max(timestamps)
        return latest if latest.tzinfo else latest.replace(tzinfo=timezone.utc)

    def _client(self):
        settings = get_settings()
        kwargs: dict[str, Any] = {
            "region_name": settings.object_storage_region,
            "config": Config(
                s3={
                    "addressing_style": (
                        "path" if settings.object_storage_force_path_style else "auto"
                    )
                },
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        }
        if settings.object_storage_endpoint_url:
            kwargs["endpoint_url"] = settings.object_storage_endpoint_url
        if settings.object_storage_access_key_id:
            kwargs["aws_access_key_id"] = settings.object_storage_access_key_id
        if settings.object_storage_secret_access_key:
            kwargs["aws_secret_access_key"] = settings.object_storage_secret_access_key
        return boto3.client("s3", **kwargs)

    @staticmethod
    def _segment(value: str) -> str:
        cleaned = "".join(
            character
            for character in value
            if character.isalnum() or character in "-_"
        )
        if not cleaned:
            raise ObjectStorageError("Storage key identity is invalid.")
        return cleaned

    def _local_path(self, logical_key: str) -> Path:
        root = Path(get_settings().upload_dir).resolve()
        path = (root / Path(*PurePosixPath(logical_key).parts)).resolve()
        if root != path and root not in path.parents:
            raise ObjectStorageError("Storage key escapes the configured root.")
        return path

    def _remote_key(self, logical_key: str) -> str:
        prefix = get_settings().object_storage_prefix.strip("/")
        return f"{prefix}/{logical_key}" if prefix else logical_key

    @staticmethod
    def _validate_key(key: str) -> str:
        candidate = PurePosixPath(key.replace("\\", "/"))
        if candidate.is_absolute() or not candidate.parts or ".." in candidate.parts:
            raise ObjectStorageError("Storage key is invalid.")
        normalized = candidate.as_posix().lstrip("/")
        if not normalized or normalized == ".":
            raise ObjectStorageError("Storage key is invalid.")
        return normalized

    @staticmethod
    def _bucket() -> str:
        bucket = get_settings().object_storage_bucket
        if not bucket:
            raise ObjectStorageError("Object storage bucket is not configured.")
        return bucket


object_storage_service = ObjectStorageService()
