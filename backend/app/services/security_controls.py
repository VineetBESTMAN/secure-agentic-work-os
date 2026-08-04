from __future__ import annotations

import hashlib
import re
import socket
import struct
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from app.core.config import get_settings
from app.core.database import decode_json, encode_json, get_connection
from app.models.schemas import (
    SecurityFindingRecord,
    SecurityPolicyRecord,
    SecurityPolicyUpdateRequest,
)


class SecurityInspectionError(ValueError):
    pass


@dataclass
class SecurityInspection:
    unsafe_reasons: list[str] = field(default_factory=list)


@dataclass
class MalwareScan:
    status: str
    engine: str
    findings: list[str] = field(default_factory=list)
    error: str | None = None


class SecurityControlService:
    _api_key_patterns = (
        re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
        re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        re.compile(
            r"(?i)\b(?:api[_-]?key|access[_-]?token|client[_-]?secret)\s*[:=]\s*"
            r"[\"']?[A-Za-z0-9_./+=-]{16,}"
        ),
    )
    _private_key_pattern = re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
    )
    _ssn_pattern = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")
    _card_pattern = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
    _eicar = b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE"
    _embedded_executable_suffixes = {
        ".bat",
        ".cmd",
        ".com",
        ".dll",
        ".exe",
        ".hta",
        ".js",
        ".msi",
        ".ps1",
        ".scr",
        ".vbs",
    }

    def get_policy(self, organization_id: str) -> SecurityPolicyRecord:
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM organization_security_policies
                WHERE organization_id = ?
                """,
                (organization_id,),
            ).fetchone()
            if row is None:
                defaults = SecurityPolicyUpdateRequest(
                    malware_mode=get_settings().malware_scanner_mode
                )
                connection.execute(
                    """
                    INSERT INTO organization_security_policies (
                        policy_id, organization_id, dlp_mode, dlp_data_types_json,
                        malware_mode, retention_enabled, audit_retention_days,
                        runtime_retention_days, connector_validation_retention_days,
                        rag_evaluation_retention_days, security_finding_retention_days,
                        updated_by
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (organization_id) DO NOTHING
                    """,
                    (
                        f"secpol_{uuid4().hex}",
                        organization_id,
                        defaults.dlp_mode,
                        encode_json(defaults.dlp_data_types),
                        defaults.malware_mode,
                        defaults.retention_enabled,
                        defaults.audit_retention_days,
                        defaults.runtime_retention_days,
                        defaults.connector_validation_retention_days,
                        defaults.rag_evaluation_retention_days,
                        defaults.security_finding_retention_days,
                        "system",
                    ),
                )
                row = connection.execute(
                    """
                    SELECT * FROM organization_security_policies
                    WHERE organization_id = ?
                    """,
                    (organization_id,),
                ).fetchone()
        if row is None:
            raise RuntimeError("Security policy could not be initialized.")
        return self._row_to_policy(row)

    def update_policy(
        self,
        organization_id: str,
        actor_id: str,
        payload: SecurityPolicyUpdateRequest,
    ) -> SecurityPolicyRecord:
        current = self.get_policy(organization_id)
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE organization_security_policies
                SET dlp_mode = ?, dlp_data_types_json = ?, malware_mode = ?,
                    retention_enabled = ?, audit_retention_days = ?,
                    runtime_retention_days = ?,
                    connector_validation_retention_days = ?,
                    rag_evaluation_retention_days = ?,
                    security_finding_retention_days = ?, updated_by = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE policy_id = ? AND organization_id = ?
                """,
                (
                    payload.dlp_mode,
                    encode_json(payload.dlp_data_types),
                    payload.malware_mode,
                    payload.retention_enabled,
                    payload.audit_retention_days,
                    payload.runtime_retention_days,
                    payload.connector_validation_retention_days,
                    payload.rag_evaluation_retention_days,
                    payload.security_finding_retention_days,
                    actor_id,
                    current.policy_id,
                    organization_id,
                ),
            )
        return self.get_policy(organization_id)

    def preflight_upload(
        self,
        *,
        filename: str,
        data: bytes,
        actor_id: str,
        organization_id: str,
    ) -> None:
        self._validate_size(data)
        policy = self.get_policy(organization_id)
        malware = self._scan_malware(filename, data, policy.malware_mode)
        self._enforce_malware(
            malware=malware,
            filename=filename,
            data=data,
            actor_id=actor_id,
            organization_id=organization_id,
        )

    def inspect_upload(
        self,
        *,
        filename: str,
        data: bytes,
        text: str,
        actor_id: str,
        organization_id: str,
    ) -> SecurityInspection:
        self.preflight_upload(
            filename=filename,
            data=data,
            actor_id=actor_id,
            organization_id=organization_id,
        )
        policy = self.get_policy(organization_id)
        if policy.dlp_mode == "disabled":
            return SecurityInspection()

        findings = self._scan_dlp(text, set(policy.dlp_data_types))
        if not findings:
            return SecurityInspection()
        action = "blocked" if policy.dlp_mode == "block" else "audited"
        self._record_finding(
            organization_id=organization_id,
            actor_id=actor_id,
            control_type="dlp",
            action=action,
            resource_name=Path(filename).name,
            data=data,
            findings=findings,
        )
        detected_types = sorted({str(item["type"]) for item in findings})
        if action == "blocked":
            raise SecurityInspectionError(
                "Upload blocked by the organization's data-loss prevention policy."
            )
        return SecurityInspection(
            unsafe_reasons=[f"DLP detected: {item}" for item in detected_types]
        )

    def list_findings(
        self, organization_id: str, limit: int = 100
    ) -> list[SecurityFindingRecord]:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM security_findings
                WHERE organization_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (organization_id, min(max(limit, 1), 500)),
            ).fetchall()
        return [
            SecurityFindingRecord(
                finding_id=row["finding_id"],
                organization_id=row["organization_id"],
                actor_id=row["actor_id"],
                control_type=row["control_type"],
                action=row["action"],
                resource_type=row["resource_type"],
                resource_name=row["resource_name"],
                content_hash=row["content_hash"],
                findings=decode_json(row["findings_json"], []),
                created_at=str(row["created_at"]) if row["created_at"] else None,
            )
            for row in rows
        ]

    def _validate_size(self, data: bytes) -> None:
        if len(data) > get_settings().upload_max_bytes:
            raise SecurityInspectionError(
                f"Upload exceeds the {get_settings().upload_max_bytes}-byte security limit."
            )

    def _scan_malware(self, filename: str, data: bytes, mode: str) -> MalwareScan:
        if mode == "disabled":
            return MalwareScan(status="clean", engine="disabled")
        if mode == "clamav":
            return self._scan_clamav(data)
        findings: list[str] = []
        lowered = data.lower()
        suffix = Path(filename).suffix.lower()
        if self._eicar.lower() in lowered:
            findings.append("known-test-signature")
        if suffix != ".docx" and (data.startswith(b"MZ") or data.startswith(b"\x7fELF")):
            findings.append("executable-content")
        if suffix == ".pdf" and any(
            marker in lowered
            for marker in (b"/javascript", b"/launch", b"/embeddedfile")
        ):
            findings.append("active-pdf-content")
        if zipfile.is_zipfile(BytesIO(data)):
            try:
                with zipfile.ZipFile(BytesIO(data)) as archive:
                    entries = archive.infolist()
                    if len(entries) > 2_000:
                        findings.append("archive-entry-limit")
                    expanded = sum(item.file_size for item in entries)
                    if expanded > get_settings().upload_max_bytes * 10:
                        findings.append("archive-expansion-limit")
                    if any(
                        Path(item.filename).suffix.lower()
                        in self._embedded_executable_suffixes
                        for item in entries
                    ):
                        findings.append("embedded-executable")
            except (OSError, zipfile.BadZipFile):
                findings.append("malformed-archive")
        return MalwareScan(
            status="infected" if findings else "clean",
            engine="basic-signature-v1",
            findings=findings,
        )

    def _scan_clamav(self, data: bytes) -> MalwareScan:
        settings = get_settings()
        try:
            with socket.create_connection(
                (settings.clamav_host, settings.clamav_port),
                timeout=settings.clamav_timeout_seconds,
            ) as connection:
                connection.sendall(b"zINSTREAM\0")
                for start in range(0, len(data), 64 * 1024):
                    chunk = data[start : start + 64 * 1024]
                    connection.sendall(struct.pack(">I", len(chunk)))
                    connection.sendall(chunk)
                connection.sendall(struct.pack(">I", 0))
                response = connection.recv(16 * 1024).decode(
                    "utf-8", errors="replace"
                )
        except OSError as exc:
            return MalwareScan(
                status="error",
                engine="clamav",
                error=type(exc).__name__,
            )
        if "FOUND" in response:
            signature = response.rsplit(":", 1)[-1].replace("FOUND", "").strip()
            return MalwareScan(
                status="infected",
                engine="clamav",
                findings=[signature or "clamav-signature"],
            )
        if "OK" not in response:
            return MalwareScan(status="error", engine="clamav", error="invalid-response")
        return MalwareScan(status="clean", engine="clamav")

    def _enforce_malware(
        self,
        *,
        malware: MalwareScan,
        filename: str,
        data: bytes,
        actor_id: str,
        organization_id: str,
    ) -> None:
        blocked = malware.status == "infected" or (
            malware.status == "error" and get_settings().malware_fail_closed
        )
        if not blocked:
            return
        findings = [
            {"type": "malware", "engine": malware.engine, "name": finding}
            for finding in malware.findings
        ]
        if malware.error:
            findings.append(
                {
                    "type": "scanner_error",
                    "engine": malware.engine,
                    "name": malware.error,
                }
            )
        self._record_finding(
            organization_id=organization_id,
            actor_id=actor_id,
            control_type="malware",
            action="blocked",
            resource_name=Path(filename).name,
            data=data,
            findings=findings,
        )
        if malware.status == "error":
            raise SecurityInspectionError(
                "Upload blocked because the required malware scanner is unavailable."
            )
        raise SecurityInspectionError("Upload blocked by malware protection.")

    def _scan_dlp(
        self, text: str, enabled_types: set[str]
    ) -> list[dict[str, object]]:
        bounded = text[: get_settings().dlp_scan_max_bytes]
        findings: list[dict[str, object]] = []
        if "api_key" in enabled_types:
            for pattern in self._api_key_patterns:
                findings.extend(self._fingerprinted_matches("api_key", pattern, bounded))
        if "private_key" in enabled_types:
            findings.extend(
                self._fingerprinted_matches(
                    "private_key", self._private_key_pattern, bounded
                )
            )
        if "ssn" in enabled_types:
            findings.extend(self._fingerprinted_matches("ssn", self._ssn_pattern, bounded))
        if "credit_card" in enabled_types:
            for match in self._card_pattern.finditer(bounded):
                candidate = re.sub(r"\D", "", match.group(0))
                if self._luhn_valid(candidate):
                    findings.append(self._finding("credit_card", candidate))
        unique: dict[tuple[str, str], dict[str, object]] = {}
        for finding in findings:
            unique[(str(finding["type"]), str(finding["fingerprint"]))] = finding
        return list(unique.values())[:100]

    def _fingerprinted_matches(
        self, finding_type: str, pattern: re.Pattern[str], text: str
    ) -> list[dict[str, object]]:
        return [
            self._finding(finding_type, match.group(0))
            for match in pattern.finditer(text)
        ]

    def _finding(self, finding_type: str, value: str) -> dict[str, object]:
        return {
            "type": finding_type,
            "fingerprint": hashlib.sha256(value.encode("utf-8")).hexdigest()[:16],
        }

    def _record_finding(
        self,
        *,
        organization_id: str,
        actor_id: str,
        control_type: str,
        action: str,
        resource_name: str,
        data: bytes,
        findings: list[dict[str, object]],
    ) -> None:
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO security_findings (
                    finding_id, organization_id, actor_id, control_type, action,
                    resource_type, resource_name, content_hash, findings_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"secfind_{uuid4().hex}",
                    organization_id,
                    actor_id,
                    control_type,
                    action,
                    "document_upload",
                    resource_name,
                    hashlib.sha256(data).hexdigest(),
                    encode_json(findings),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def _row_to_policy(self, row) -> SecurityPolicyRecord:
        return SecurityPolicyRecord(
            policy_id=row["policy_id"],
            organization_id=row["organization_id"],
            dlp_mode=row["dlp_mode"],
            dlp_data_types=decode_json(row["dlp_data_types_json"], []),
            malware_mode=row["malware_mode"],
            retention_enabled=bool(row["retention_enabled"]),
            audit_retention_days=int(row["audit_retention_days"]),
            runtime_retention_days=int(row["runtime_retention_days"]),
            connector_validation_retention_days=int(
                row["connector_validation_retention_days"]
            ),
            rag_evaluation_retention_days=int(row["rag_evaluation_retention_days"]),
            security_finding_retention_days=int(
                row["security_finding_retention_days"]
            ),
            updated_by=row["updated_by"],
            created_at=str(row["created_at"]) if row["created_at"] else None,
            updated_at=str(row["updated_at"]) if row["updated_at"] else None,
        )

    @staticmethod
    def _luhn_valid(value: str) -> bool:
        if not 13 <= len(value) <= 19 or len(set(value)) == 1:
            return False
        total = 0
        parity = len(value) % 2
        for index, character in enumerate(value):
            digit = int(character)
            if index % 2 == parity:
                digit *= 2
                if digit > 9:
                    digit -= 9
            total += digit
        return total % 10 == 0


security_control_service = SecurityControlService()
