import csv
import hashlib
import json
import re
import zipfile
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from io import BytesIO, StringIO
from pathlib import Path
from xml.etree import ElementTree

from pypdf import PdfReader


WORD_PATTERN = re.compile(r"\S+")
MARKDOWN_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
WORD_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
WORD = f"{{{WORD_NAMESPACE}}}"


@dataclass(frozen=True)
class ExtractedSection:
    text: str
    heading: str | None
    locator: str


@dataclass(frozen=True)
class PreparedChunk:
    text: str
    heading: str | None
    locator: str
    token_count: int
    content_hash: str

    @property
    def embedding_text(self) -> str:
        prefix = f"Section: {self.heading}\n" if self.heading else ""
        return f"{prefix}{self.text}".strip()


def extract_sections(filename: str, data: bytes) -> list[ExtractedSection]:
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf(data)
    if suffix == ".docx":
        return _extract_docx(data)
    if suffix == ".md":
        return _extract_markdown(_decode(data))
    if suffix == ".csv":
        return _extract_csv(_decode(data))
    if suffix == ".json":
        return _extract_json(_decode(data))
    if suffix == ".eml":
        return _extract_email(data)
    if suffix in {".txt", ".log", ".yaml", ".yml"}:
        return [ExtractedSection(text=_decode(data), heading=None, locator="document")]
    raise ValueError(
        "Unsupported file type. Upload .txt, .md, .csv, .json, .yaml, .yml, .log, "
        ".eml, .pdf, or .docx."
    )


def chunk_sections(
    sections: list[ExtractedSection],
    *,
    target_words: int = 180,
    maximum_words: int = 240,
    overlap_words: int = 30,
) -> list[PreparedChunk]:
    prepared: list[PreparedChunk] = []
    for section in sections:
        text = section.text.strip()
        if not text:
            continue
        blocks = _blocks(text)
        section_chunks: list[str] = []
        current: list[str] = []
        current_words = 0
        for block in blocks:
            block_words = len(WORD_PATTERN.findall(block))
            if block_words > maximum_words:
                if current:
                    section_chunks.append("\n\n".join(current))
                    current = []
                    current_words = 0
                section_chunks.extend(
                    _window_text(block, maximum_words, overlap_words)
                )
                continue
            if current and current_words + block_words > target_words:
                section_chunks.append("\n\n".join(current))
                overlap = current[-1] if len(WORD_PATTERN.findall(current[-1])) <= overlap_words else ""
                current = [overlap] if overlap else []
                current_words = len(WORD_PATTERN.findall(overlap)) if overlap else 0
            current.append(block)
            current_words += block_words
        if current:
            section_chunks.append("\n\n".join(current))

        for part, chunk_text in enumerate(section_chunks, start=1):
            locator = section.locator
            if len(section_chunks) > 1:
                locator = f"{locator} · part {part}"
            normalized = chunk_text.strip()
            prepared.append(
                PreparedChunk(
                    text=normalized,
                    heading=section.heading,
                    locator=locator,
                    token_count=len(WORD_PATTERN.findall(normalized)),
                    content_hash=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
                )
            )
    return _merge_adjacent_sections(prepared, target_words=300)


def joined_text(sections: list[ExtractedSection]) -> str:
    return "\n\n".join(section.text.strip() for section in sections if section.text.strip())


def _decode(data: bytes) -> str:
    return data.decode("utf-8-sig", errors="replace")


def _extract_pdf(data: bytes) -> list[ExtractedSection]:
    reader = PdfReader(BytesIO(data))
    sections = []
    for index, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        if text.strip():
            sections.append(
                ExtractedSection(text=text, heading=None, locator=f"page {index}")
            )
    return sections


def _extract_docx(data: bytes) -> list[ExtractedSection]:
    with zipfile.ZipFile(BytesIO(data)) as archive:
        xml = archive.read("word/document.xml")
    root = ElementTree.fromstring(xml)
    body = root.find(f".//{WORD}body")
    if body is None:
        return []

    sections: list[ExtractedSection] = []
    heading: str | None = None
    paragraph_number = 0
    table_number = 0
    buffered: list[str] = []

    def flush(locator: str) -> None:
        if buffered:
            sections.append(
                ExtractedSection(
                    text="\n".join(buffered),
                    heading=heading,
                    locator=locator,
                )
            )
            buffered.clear()

    for child in body:
        if child.tag == f"{WORD}p":
            paragraph_number += 1
            text = "".join(node.text or "" for node in child.iter(f"{WORD}t")).strip()
            if not text:
                continue
            style = child.find(f"./{WORD}pPr/{WORD}pStyle")
            style_name = style.get(f"{WORD}val", "") if style is not None else ""
            if style_name.casefold().startswith("heading"):
                flush(f"paragraph {max(1, paragraph_number - len(buffered))}")
                heading = text
            else:
                buffered.append(text)
        elif child.tag == f"{WORD}tbl":
            flush(f"paragraph {max(1, paragraph_number - len(buffered) + 1)}")
            table_number += 1
            rows = []
            for row in child.iter(f"{WORD}tr"):
                cells = []
                for cell in row.iter(f"{WORD}tc"):
                    value = " ".join(
                        node.text or "" for node in cell.iter(f"{WORD}t")
                    ).strip()
                    cells.append(value)
                if any(cells):
                    rows.append(" | ".join(cells))
            if rows:
                sections.append(
                    ExtractedSection(
                        text="\n".join(rows),
                        heading=heading,
                        locator=f"table {table_number}",
                    )
                )
    flush(f"paragraph {max(1, paragraph_number - len(buffered) + 1)}")
    return sections


def _extract_markdown(text: str) -> list[ExtractedSection]:
    sections: list[ExtractedSection] = []
    hierarchy: dict[int, str] = {}
    buffered: list[str] = []
    current_heading: str | None = None

    def flush() -> None:
        if any(line.strip() for line in buffered):
            sections.append(
                ExtractedSection(
                    text="\n".join(buffered),
                    heading=current_heading,
                    locator=f"section {current_heading}" if current_heading else "document",
                )
            )
        buffered.clear()

    for line in text.splitlines():
        match = MARKDOWN_HEADING.match(line)
        if not match:
            buffered.append(line)
            continue
        flush()
        level = len(match.group(1))
        hierarchy[level] = match.group(2).strip()
        hierarchy = {key: value for key, value in hierarchy.items() if key <= level}
        current_heading = " > ".join(hierarchy[key] for key in sorted(hierarchy))
    flush()
    return sections


def _extract_csv(text: str) -> list[ExtractedSection]:
    rows = list(csv.reader(StringIO(text)))
    if not rows:
        return []
    header = rows[0]
    sections = []
    for start in range(1, len(rows), 25):
        batch = rows[start : start + 25]
        rendered = [" | ".join(header), *(" | ".join(row) for row in batch)]
        sections.append(
            ExtractedSection(
                text="\n".join(rendered),
                heading="CSV rows",
                locator=f"rows {start + 1}-{start + len(batch)}",
            )
        )
    if len(rows) == 1:
        sections.append(
            ExtractedSection(text=" | ".join(header), heading="CSV header", locator="row 1")
        )
    return sections


def _extract_json(text: str) -> list[ExtractedSection]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return [ExtractedSection(text=text, heading=None, locator="document")]
    formatted = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    return [ExtractedSection(text=formatted, heading="JSON document", locator="document")]


def _extract_email(data: bytes) -> list[ExtractedSection]:
    message = BytesParser(policy=policy.default).parsebytes(data)
    headers = "\n".join(
        f"{name}: {message.get(name, '')}" for name in ("From", "To", "Subject", "Date")
    )
    body = message.get_body(preferencelist=("plain",)) if message.is_multipart() else message
    content = body.get_content() if body is not None else ""
    return [
        ExtractedSection(
            text=f"{headers}\n\n{content}",
            heading=str(message.get("Subject") or "Email"),
            locator="email body",
        )
    ]


def _blocks(text: str) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    if len(paragraphs) > 1:
        return paragraphs
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    return lines or [text.strip()]


def _window_text(text: str, size: int, overlap: int) -> list[str]:
    words = WORD_PATTERN.findall(text)
    step = max(1, size - overlap)
    windows = []
    for start in range(0, len(words), step):
        window = " ".join(words[start : start + size])
        if window:
            windows.append(window)
        if start + size >= len(words):
            break
    return windows


def _merge_adjacent_sections(
    chunks: list[PreparedChunk], *, target_words: int
) -> list[PreparedChunk]:
    """Reduce tiny-section inference overhead while retaining inline structure labels."""
    merged: list[PreparedChunk] = []
    for chunk in chunks:
        if not merged:
            merged.append(chunk)
            continue
        previous = merged[-1]
        mergeable_locator = (
            previous.locator.startswith("section ")
            and chunk.locator.startswith("section ")
            and " – " not in previous.locator
        )
        previous_root = (previous.heading or "").split(" > ", 1)[0]
        current_root = (chunk.heading or "").split(" > ", 1)[0]
        if (
            not mergeable_locator
            or previous_root != current_root
            or previous.token_count + chunk.token_count > target_words
        ):
            merged.append(chunk)
            continue

        parts = []
        for item in (previous, chunk):
            if item.heading:
                parts.append(f"## {item.heading}\n{item.text}")
            else:
                parts.append(item.text)
        text = "\n\n".join(parts)
        headings = list(
            dict.fromkeys(
                heading for heading in (previous.heading, chunk.heading) if heading
            )
        )
        heading = " | ".join(headings)
        locator = f"{previous.locator} – {chunk.locator}"
        merged[-1] = PreparedChunk(
            text=text,
            heading=heading or None,
            locator=locator,
            token_count=previous.token_count + chunk.token_count,
            content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        )
    return merged
