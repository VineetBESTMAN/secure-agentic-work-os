import math
import re
import zipfile
from collections import Counter
from io import BytesIO
from pathlib import Path
from uuid import uuid4
from xml.etree import ElementTree

from pypdf import PdfReader

from app.core.config import get_settings
from app.core.database import decode_json, encode_json, get_connection
from app.models.schemas import Citation, DocumentRecord, RagAnswer
from app.services.prompt_guard import prompt_guard_service

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
    return DocumentRecord(
        document_id=row["document_id"],
        title=row["title"],
        filename=row["filename"],
        classification=row["classification"],
        owner_team=row["owner_team"],
        summary=row["summary"],
        unsafe=bool(row["unsafe"]),
        unsafe_reasons=decode_json(row["unsafe_reasons_json"], []),
        chunk_count=row["chunk_count"],
        created_at=row["created_at"],
    )


class RagService:
    def ingest_file(
        self,
        filename: str,
        data: bytes,
        classification: str,
        owner_team: str,
        uploaded_by: str,
    ) -> DocumentRecord:
        text = _extract_text(filename=filename, data=data)
        cleaned = _clean_text(text)
        if not cleaned:
            raise ValueError("No readable text was found in this file.")

        chunks = _chunk_text(cleaned)
        if not chunks:
            raise ValueError("No searchable chunks could be created from this file.")

        settings = get_settings()
        upload_dir = Path(settings.upload_dir)
        upload_dir.mkdir(parents=True, exist_ok=True)

        document_id = f"doc_{uuid4().hex}"
        stored_name = f"{document_id}_{Path(filename).name}"
        (upload_dir / stored_name).write_bytes(data)

        scan = prompt_guard_service.scan_text(cleaned[:20_000])
        title = Path(filename).stem.replace("_", " ").replace("-", " ").strip() or filename

        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO documents (
                    document_id, title, filename, classification, owner_team, summary,
                    uploaded_by, unsafe, unsafe_reasons_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document_id,
                    title,
                    filename,
                    classification,
                    owner_team,
                    _summary(cleaned),
                    uploaded_by,
                    int(scan.flagged),
                    encode_json(scan.reasons),
                ),
            )
            connection.executemany(
                """
                INSERT INTO document_chunks (chunk_id, document_id, chunk_index, text)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (f"chk_{uuid4().hex}", document_id, index, chunk)
                    for index, chunk in enumerate(chunks)
                ],
            )

        return self.get_document(document_id=document_id)

    def list_documents(self, role: str) -> list[DocumentRecord]:
        where_clause = ""
        params: tuple[str, ...] = ()
        if role != "admin":
            where_clause = "WHERE d.classification != ?"
            params = ("restricted",)

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

    def get_document(self, document_id: str) -> DocumentRecord:
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT d.*, COUNT(c.chunk_id) AS chunk_count
                FROM documents d
                LEFT JOIN document_chunks c ON c.document_id = d.document_id
                WHERE d.document_id = ?
                GROUP BY d.document_id
                """,
                (document_id,),
            ).fetchone()
        if row is None:
            raise ValueError("Document was not found after ingestion.")
        return _row_to_document(row)

    def answer(self, question: str, role: str) -> RagAnswer:
        query_tokens = _tokens(question)
        if not query_tokens:
            return RagAnswer(answer="Ask a more specific question.", citations=[])

        rows = self._visible_chunks(role=role)
        if not rows:
            return RagAnswer(
                answer="No accessible, safe documents have been uploaded yet.",
                citations=[],
            )

        tokenized_chunks = [(row, Counter(_tokens(row["text"]))) for row in rows]
        document_frequency: Counter[str] = Counter()
        for _, counts in tokenized_chunks:
            for token in counts:
                document_frequency[token] += 1

        total_chunks = len(tokenized_chunks)
        scored = []
        for row, counts in tokenized_chunks:
            score = 0.0
            for token in query_tokens:
                if token not in counts:
                    continue
                idf = math.log((total_chunks + 1) / (document_frequency[token] + 1)) + 1
                score += counts[token] * idf
            if score > 0:
                scored.append((score, row))

        scored.sort(key=lambda item: item[0], reverse=True)
        top_matches = scored[:3]
        if not top_matches:
            return RagAnswer(
                answer="I could not find a relevant passage in your accessible documents.",
                citations=[],
            )

        citations = [
            Citation(
                document_id=row["document_id"],
                title=row["title"],
                excerpt=_summary(row["text"], limit=420),
                chunk_id=row["chunk_id"],
                score=round(score, 3),
            )
            for score, row in top_matches
        ]
        source_names = ", ".join(dict.fromkeys(citation.title for citation in citations))
        return RagAnswer(
            answer=f"Based on the retrieved passages, the strongest sources are: {source_names}.",
            citations=citations,
        )

    def _visible_chunks(self, role: str):
        where = ["d.unsafe = 0"]
        params: list[str] = []
        if role != "admin":
            where.append("d.classification != ?")
            params.append("restricted")

        with get_connection() as connection:
            return connection.execute(
                f"""
                SELECT c.chunk_id, c.text, d.document_id, d.title
                FROM document_chunks c
                JOIN documents d ON d.document_id = c.document_id
                WHERE {' AND '.join(where)}
                """,
                tuple(params),
            ).fetchall()


rag_service = RagService()
