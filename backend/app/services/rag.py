import math
import re
import time
import zipfile
from io import BytesIO
from pathlib import Path
from uuid import uuid4
from xml.etree import ElementTree

from pypdf import PdfReader

from app.core.config import get_settings
from app.core.database import (
    decode_json,
    encode_json,
    get_connection,
    is_postgres_database,
    vector_literal,
)
from app.models.schemas import (
    Citation,
    DocumentChunkRecord,
    DocumentDetail,
    DocumentRecord,
    DocumentUpdateRequest,
    RagAnswer,
)
from app.services.embeddings import embedding_service
from app.services.grounded_answers import grounded_answer_service
from app.services.observability import observability_service
from app.services.object_storage import ObjectStorageError, object_storage_service
from app.services.policies import policy_service
from app.services.prompt_guard import prompt_guard_service
from app.services.security_controls import security_control_service

STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "we",
    "what",
    "when",
    "where",
    "who",
    "why",
    "with",
}


def _tokens(text: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[a-zA-Z0-9][a-zA-Z0-9_-]{1,}", text.lower())
        if token not in STOP_WORDS
    ]


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _summary(text: str, limit: int = 280) -> str:
    cleaned = _clean_text(text)
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rsplit(" ", 1)[0] + "..."


def _chunk_text(text: str, chunk_size: int = 220, overlap: int = 40) -> list[str]:
    words = _clean_text(text).split()
    if not words:
        return []

    chunks: list[str] = []
    step = max(1, chunk_size - overlap)
    for start in range(0, len(words), step):
        chunk = " ".join(words[start : start + chunk_size])
        if chunk:
            chunks.append(chunk)
        if start + chunk_size >= len(words):
            break
    return chunks


def _delete_storage_safely(key: str, *, backend: str) -> None:
    """Best-effort cleanup must never mask a committed database operation."""
    try:
        object_storage_service.delete(key, backend=backend)
    except ObjectStorageError:
        pass


def _extract_docx(data: bytes) -> str:
    with zipfile.ZipFile(BytesIO(data)) as archive:
        xml = archive.read("word/document.xml")
    root = ElementTree.fromstring(xml)
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs = []
    for paragraph in root.findall(".//w:p", namespace):
        parts = [
            node.text or ""
            for node in paragraph.findall(".//w:t", namespace)
            if node.text
        ]
        if parts:
            paragraphs.append("".join(parts))
    return "\n".join(paragraphs)


def _extract_pdf(data: bytes) -> str:
    reader = PdfReader(BytesIO(data))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _extract_text(filename: str, data: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf(data)
    if suffix == ".docx":
        return _extract_docx(data)
    if suffix in {".txt", ".md", ".csv", ".json", ".log", ".eml"}:
        return data.decode("utf-8", errors="replace")
    raise ValueError(
        "Unsupported file type. Upload .txt, .md, .csv, .json, .eml, .pdf, or .docx."
    )


def _row_to_document(row) -> DocumentRecord:
    created_at = row["created_at"]
    return DocumentRecord(
        document_id=row["document_id"],
        title=row["title"],
        filename=row["filename"],
        classification=row["classification"],
        owner_team=row["owner_team"],
        summary=row["summary"],
        unsafe=bool(row["unsafe"]),
        unsafe_reasons=decode_json(row["unsafe_reasons_json"], []),
        storage_backend=row["storage_backend"],
        storage_key=row["storage_key"],
        chunk_count=row["chunk_count"],
        created_at=str(created_at) if created_at is not None else None,
    )


class RagService:
    def ingest_file(
        self,
        filename: str,
        data: bytes,
        classification: str,
        owner_team: str,
        uploaded_by: str,
        document_id: str | None = None,
        organization_id: str = "org_default",
    ) -> DocumentRecord:
        if document_id:
            try:
                return self.get_document(
                    document_id=document_id, organization_id=organization_id
                )
            except ValueError:
                pass

        text = _extract_text(filename=filename, data=data)
        cleaned = _clean_text(text)
        if not cleaned:
            raise ValueError("No readable text was found in this file.")
        security_inspection = security_control_service.inspect_upload(
            filename=filename,
            data=data,
            text=cleaned,
            actor_id=uploaded_by,
            organization_id=organization_id,
        )

        chunks = _chunk_text(cleaned)
        if not chunks:
            raise ValueError("No searchable chunks could be created from this file.")

        document_id = document_id or f"doc_{uuid4().hex}"
        safe_filename = Path(filename).name or "uploaded-document.txt"
        storage_key = object_storage_service.document_key(
            organization_id=organization_id,
            document_id=document_id,
            filename=safe_filename,
        )
        object_storage_service.put(storage_key, data, filename=safe_filename)
        storage_backend = get_settings().object_storage_backend

        scan = prompt_guard_service.scan_text(cleaned[:20_000])
        unsafe_reasons = list(
            dict.fromkeys([*scan.reasons, *security_inspection.unsafe_reasons])
        )
        title = (
            Path(filename).stem.replace("_", " ").replace("-", " ").strip()
            or filename
        )

        try:
            with get_connection() as connection:
                connection.execute(
                    """
                    INSERT INTO documents (
                        document_id, title, filename, classification, owner_team, summary,
                        uploaded_by, unsafe, unsafe_reasons_json, organization_id,
                        storage_backend, storage_key
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        document_id,
                        title,
                        safe_filename,
                        classification,
                        owner_team,
                        _summary(cleaned),
                        uploaded_by,
                        bool(unsafe_reasons),
                        encode_json(unsafe_reasons),
                        organization_id,
                        storage_backend,
                        storage_key,
                    ),
                )
                with observability_service.context(
                    uploaded_by, organization_id=organization_id
                ):
                    self._insert_chunks(
                        connection,
                        document_id=document_id,
                        chunks=chunks,
                        organization_id=organization_id,
                    )
        except Exception:
            _delete_storage_safely(storage_key, backend=storage_backend)
            raise

        return self.get_document(document_id=document_id, organization_id=organization_id)

    def list_documents(
        self, role: str, organization_id: str = "org_default"
    ) -> list[DocumentRecord]:
        where_clause = "WHERE d.organization_id = ?"
        params: tuple[str, ...] = (organization_id,)
        if role != "admin":
            where_clause += " AND d.classification != ?"
            params += ("restricted",)

        with get_connection() as connection:
            rows = connection.execute(
                f"""
                SELECT d.*, COUNT(c.chunk_id) AS chunk_count
                FROM documents d
                LEFT JOIN document_chunks c ON c.document_id = d.document_id
                {where_clause}
                GROUP BY d.document_id
                ORDER BY d.created_at DESC
                """,
                params,
            ).fetchall()
        return [_row_to_document(row) for row in rows]

    def replace_file_content(
        self,
        *,
        document_id: str,
        filename: str,
        data: bytes,
        classification: str,
        owner_team: str,
        uploaded_by: str,
        organization_id: str = "org_default",
    ) -> DocumentRecord:
        """Replace a connector-managed document in place while preserving its identity."""
        existing = self.get_document(
            document_id=document_id, organization_id=organization_id
        )
        cleaned = _clean_text(_extract_text(filename=filename, data=data))
        if not cleaned:
            raise ValueError("No readable text was found in this file.")
        security_inspection = security_control_service.inspect_upload(
            filename=filename,
            data=data,
            text=cleaned,
            actor_id=uploaded_by,
            organization_id=organization_id,
        )
        chunks = _chunk_text(cleaned)
        if not chunks:
            raise ValueError("No searchable chunks could be created from this file.")

        safe_filename = Path(filename).name or "connector-item.txt"
        new_key = object_storage_service.document_key(
            organization_id=organization_id,
            document_id=document_id,
            filename=safe_filename,
        )
        old_key = existing.storage_key or object_storage_service.legacy_document_key(
            document_id=document_id, filename=existing.filename
        )
        if new_key == old_key:
            new_key = f"{new_key}.{uuid4().hex}"
        new_backend = get_settings().object_storage_backend
        object_storage_service.put(new_key, data, filename=safe_filename)

        scan = prompt_guard_service.scan_text(cleaned[:20_000])
        unsafe_reasons = list(
            dict.fromkeys([*scan.reasons, *security_inspection.unsafe_reasons])
        )
        title = (
            Path(safe_filename).stem.replace("_", " ").replace("-", " ").strip()
            or safe_filename
        )
        try:
            with get_connection() as connection:
                connection.execute(
                    "DELETE FROM document_chunks WHERE document_id = ? AND organization_id = ?",
                    (document_id, organization_id),
                )
                connection.execute(
                    """
                    UPDATE documents
                    SET title = ?, filename = ?, classification = ?, owner_team = ?,
                        summary = ?, uploaded_by = ?, unsafe = ?, unsafe_reasons_json = ?,
                        storage_backend = ?, storage_key = ?
                    WHERE document_id = ? AND organization_id = ?
                    """,
                    (
                        title,
                        safe_filename,
                        classification,
                        owner_team,
                        _summary(cleaned),
                        uploaded_by,
                        bool(unsafe_reasons),
                        encode_json(unsafe_reasons),
                        new_backend,
                        new_key,
                        document_id,
                        organization_id,
                    ),
                )
                with observability_service.context(
                    uploaded_by, organization_id=organization_id
                ):
                    self._insert_chunks(
                        connection,
                        document_id=document_id,
                        chunks=chunks,
                        organization_id=organization_id,
                    )
        except Exception:
            _delete_storage_safely(new_key, backend=new_backend)
            raise

        _delete_storage_safely(old_key, backend=existing.storage_backend)
        return self.get_document(
            document_id=document_id, organization_id=organization_id
        )

    def get_document(
        self, document_id: str, organization_id: str = "org_default"
    ) -> DocumentRecord:
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT d.*, COUNT(c.chunk_id) AS chunk_count
                FROM documents d
                LEFT JOIN document_chunks c ON c.document_id = d.document_id
                WHERE d.document_id = ? AND d.organization_id = ?
                GROUP BY d.document_id
                """,
                (document_id, organization_id),
            ).fetchone()
        if row is None:
            raise ValueError("Document was not found after ingestion.")
        return _row_to_document(row)

    def get_document_detail(
        self, document_id: str, role: str, organization_id: str = "org_default"
    ) -> DocumentDetail:
        document = self.get_document(
            document_id=document_id, organization_id=organization_id
        )
        if not self.can_access_document(
            document=document, role=role, organization_id=organization_id
        ):
            raise PermissionError("You do not have access to this document.")

        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT chunk_id, chunk_index, text
                FROM document_chunks
                WHERE document_id = ? AND organization_id = ?
                ORDER BY chunk_index ASC
                """,
                (document_id, organization_id),
            ).fetchall()

        return DocumentDetail(
            **document.model_dump(),
            chunks=[
                DocumentChunkRecord(
                    chunk_id=row["chunk_id"],
                    chunk_index=row["chunk_index"],
                    text=row["text"],
                )
                for row in rows
            ],
        )

    def list_unsafe_documents(
        self, organization_id: str = "org_default"
    ) -> list[DocumentRecord]:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT d.*, COUNT(c.chunk_id) AS chunk_count
                FROM documents d
                LEFT JOIN document_chunks c ON c.document_id = d.document_id
                WHERE d.unsafe = ? AND d.organization_id = ?
                GROUP BY d.document_id
                ORDER BY d.created_at DESC
                """,
                (True, organization_id),
            ).fetchall()
        return [_row_to_document(row) for row in rows]

    def update_document(
        self,
        document_id: str,
        payload: DocumentUpdateRequest,
        role: str,
        organization_id: str = "org_default",
    ) -> DocumentRecord:
        document = self.get_document(
            document_id=document_id, organization_id=organization_id
        )
        if not self.can_access_document(
            document=document, role=role, organization_id=organization_id
        ):
            raise PermissionError("You do not have access to this document.")

        updates: list[str] = []
        params: list[str] = []
        if payload.title is not None:
            updates.append("title = ?")
            params.append(payload.title.strip() or document.title)
        if payload.classification is not None:
            updates.append("classification = ?")
            params.append(payload.classification)
        if payload.owner_team is not None:
            updates.append("owner_team = ?")
            params.append(payload.owner_team.strip() or document.owner_team)

        if updates:
            params.extend((document_id, organization_id))
            with get_connection() as connection:
                connection.execute(
                    f"UPDATE documents SET {', '.join(updates)} "
                    "WHERE document_id = ? AND organization_id = ?",
                    tuple(params),
                )
        return self.get_document(document_id=document_id, organization_id=organization_id)

    def delete_document(
        self, document_id: str, role: str, organization_id: str = "org_default"
    ) -> None:
        document = self.get_document(
            document_id=document_id, organization_id=organization_id
        )
        if not self.can_access_document(
            document=document, role=role, organization_id=organization_id
        ):
            raise PermissionError("You do not have access to this document.")

        with get_connection() as connection:
            connection.execute(
                "DELETE FROM documents WHERE document_id = ? AND organization_id = ?",
                (document_id, organization_id),
            )

        storage_key = document.storage_key or object_storage_service.legacy_document_key(
            document_id=document_id, filename=document.filename
        )
        _delete_storage_safely(storage_key, backend=document.storage_backend)

    def reindex_document(
        self, document_id: str, role: str, organization_id: str = "org_default"
    ) -> DocumentRecord:
        document = self.get_document(
            document_id=document_id, organization_id=organization_id
        )
        if not self.can_access_document(
            document=document, role=role, organization_id=organization_id
        ):
            raise PermissionError("You do not have access to this document.")

        storage_key = document.storage_key or object_storage_service.legacy_document_key(
            document_id=document_id, filename=document.filename
        )
        try:
            file_data = object_storage_service.get(
                storage_key, backend=document.storage_backend
            )
        except ObjectStorageError as exc:
            raise ValueError(
                "The original uploaded file is missing from object storage."
            ) from exc
        cleaned = _clean_text(_extract_text(filename=document.filename, data=file_data))
        if not cleaned:
            raise ValueError("No readable text was found in this file.")
        security_inspection = security_control_service.inspect_upload(
            filename=document.filename,
            data=file_data,
            text=cleaned,
            actor_id=document.document_id,
            organization_id=organization_id,
        )

        chunks = _chunk_text(cleaned)
        if not chunks:
            raise ValueError("No searchable chunks could be created from this file.")

        scan = prompt_guard_service.scan_text(cleaned[:20_000])
        unsafe_reasons = list(
            dict.fromkeys([*scan.reasons, *security_inspection.unsafe_reasons])
        )
        with get_connection() as connection:
            connection.execute(
                "DELETE FROM document_chunks WHERE document_id = ? AND organization_id = ?",
                (document_id, organization_id),
            )
            connection.execute(
                """
                UPDATE documents
                SET summary = ?, unsafe = ?, unsafe_reasons_json = ?
                WHERE document_id = ? AND organization_id = ?
                """,
                (
                    _summary(cleaned),
                    bool(unsafe_reasons),
                    encode_json(unsafe_reasons),
                    document_id,
                    organization_id,
                ),
            )
            with observability_service.context(
                document.document_id, organization_id=organization_id
            ):
                self._insert_chunks(
                    connection,
                    document_id=document_id,
                    chunks=chunks,
                    organization_id=organization_id,
                )
        return self.get_document(document_id=document_id, organization_id=organization_id)

    def can_access_document(
        self,
        document: DocumentRecord,
        role: str,
        organization_id: str = "org_default",
    ) -> bool:
        return policy_service.document_access_allowed(
            user=None,
            role=role,
            classification=document.classification,
            organization_id=organization_id,
        )

    def answer(
        self,
        question: str,
        role: str,
        actor_id: str = "system",
        organization_id: str = "org_default",
    ) -> RagAnswer:
        started = time.perf_counter()
        with observability_service.context(
            actor_id,
            trace_id=observability_service.current_trace_id,
            organization_id=organization_id,
        ) as trace_id:
            try:
                query_embedding = embedding_service.embed(question)
                if not any(query_embedding):
                    answer = RagAnswer(answer="Ask a more specific question.", citations=[])
                else:
                    top_matches = self._vector_matches(
                        query_embedding=query_embedding,
                        role=role,
                        organization_id=organization_id,
                    )
                    if not top_matches:
                        answer = RagAnswer(
                            answer=(
                                "I could not find a relevant passage in your accessible "
                                "documents."
                            ),
                            citations=[],
                        )
                    else:
                        citations = [
                            self._match_to_citation(score=score, row=row)
                            for score, row in top_matches
                        ]
                        answer = grounded_answer_service.generate(
                            question=question,
                            citations=citations,
                            actor_id=actor_id,
                            organization_id=organization_id,
                        )
            except Exception as exc:
                observability_service.record_safely(
                    operation_type="rag_query",
                    provider="retrieval",
                    model="semantic-search",
                    status="failed",
                    latency_ms=(time.perf_counter() - started) * 1_000,
                    input_units=max(1, math.ceil(len(question) / 4)),
                    metadata={"role": role, "error": str(exc)},
                    trace_id=trace_id,
                )
                raise

            observability_service.record_safely(
                operation_type="rag_query",
                provider="retrieval",
                model="semantic-search",
                status="completed",
                latency_ms=(time.perf_counter() - started) * 1_000,
                input_units=max(1, math.ceil(len(question) / 4)),
                output_units=len(answer.citations),
                metadata={
                    "role": role,
                    "citation_count": len(answer.citations),
                    "generation_mode": answer.generation_mode,
                    "grounded": answer.grounded,
                },
                trace_id=trace_id,
            )
            return answer

    def _insert_chunks(
        self,
        connection,
        document_id: str,
        chunks: list[str],
        organization_id: str = "org_default",
    ) -> None:
        embeddings = embedding_service.embed_many(chunks)
        if is_postgres_database():
            connection.executemany(
                """
                INSERT INTO document_chunks (
                    chunk_id, document_id, chunk_index, text, embedding, organization_id
                ) VALUES (?, ?, ?, ?, ?::vector, ?)
                """,
                [
                    (
                        f"chk_{uuid4().hex}",
                        document_id,
                        index,
                        chunk,
                        vector_literal(embeddings[index]),
                        organization_id,
                    )
                    for index, chunk in enumerate(chunks)
                ],
            )
            return

        connection.executemany(
            """
            INSERT INTO document_chunks (
                chunk_id, document_id, chunk_index, text, embedding_json, organization_id
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    f"chk_{uuid4().hex}",
                    document_id,
                    index,
                    chunk,
                    encode_json(embeddings[index]),
                    organization_id,
                )
                for index, chunk in enumerate(chunks)
            ],
        )

    def _vector_matches(
        self, query_embedding: list[float], role: str, organization_id: str
    ):
        if is_postgres_database():
            return self._postgres_vector_matches(
                query_embedding=query_embedding, role=role, organization_id=organization_id
            )
        return self._sqlite_vector_matches(
            query_embedding=query_embedding, role=role, organization_id=organization_id
        )

    def _postgres_vector_matches(
        self, query_embedding: list[float], role: str, organization_id: str
    ):
        where = ["d.unsafe = FALSE", "c.embedding IS NOT NULL", "d.organization_id = ?"]
        params: list[str] = [organization_id]
        if role != "admin":
            where.append("d.classification != ?")
            params.append("restricted")

        embedding = vector_literal(query_embedding)
        with get_connection() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    c.chunk_id,
                    c.text,
                    d.document_id,
                    d.title,
                    1 - (c.embedding <=> ?::vector) AS score
                FROM document_chunks c
                JOIN documents d ON d.document_id = c.document_id
                WHERE {' AND '.join(where)}
                ORDER BY c.embedding <=> ?::vector
                LIMIT 3
                """,
                (embedding, *params, embedding),
            ).fetchall()
        return [(float(row["score"]), row) for row in rows if float(row["score"]) > 0]

    def _sqlite_vector_matches(
        self, query_embedding: list[float], role: str, organization_id: str
    ):
        where = ["d.unsafe = 0", "d.organization_id = ?"]
        params: list[str] = [organization_id]
        if role != "admin":
            where.append("d.classification != ?")
            params.append("restricted")

        with get_connection() as connection:
            rows = connection.execute(
                f"""
                SELECT c.chunk_id, c.text, c.embedding_json, d.document_id, d.title
                FROM document_chunks c
                JOIN documents d ON d.document_id = c.document_id
                WHERE {' AND '.join(where)}
                """,
                tuple(params),
            ).fetchall()

        scored = []
        for row in rows:
            embedding = decode_json(row["embedding_json"], None)
            if not embedding:
                embedding = embedding_service.embed(row["text"])
            score = embedding_service.cosine_similarity(query_embedding, embedding)
            if score > 0:
                scored.append((score, row))

        scored.sort(key=lambda item: item[0], reverse=True)
        return scored[:3]

    def _match_to_citation(self, score: float, row) -> Citation:
        return Citation(
            document_id=row["document_id"],
            title=row["title"],
            excerpt=_summary(row["text"], limit=420),
            chunk_id=row["chunk_id"],
            score=round(score, 3),
        )

rag_service = RagService()
