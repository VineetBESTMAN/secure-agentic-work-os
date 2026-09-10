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
from pypdf.errors import PdfReadError


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
    try:
        return _extract_sections(filename, data)
    except (PdfReadError, zipfile.BadZipFile, KeyError, ElementTree.ParseError,
            UnicodeError, csv.Error) as exc:
        raise ValueError(
            "Could not read the document. Check its format and encoding, and export it again."
        ) from exc


def _extract_sections(filename: str, data: bytes) -> list[ExtractedSection]:
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
    if not 0 <= overlap_words < target_words <= maximum_words:
        raise ValueError("Chunk sizes must satisfy 0 <= overlap < target <= maximum.")
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
                overlap = current[-1] if (
                    len(WORD_PATTERN.findall(current[-1])) <= overlap_words
                    and len(WORD_PATTERN.findall(current[-1])) + block_words <= maximum_words
                ) else ""
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
    return prepared


def joined_text(sections: list[ExtractedSection]) -> str:
    return "\n\n".join(
        "\n".join(part for part in (section.heading, section.text.strip()) if part)
        for section in sections if section.text.strip() or section.heading
    )


def _decode(data: bytes) -> str:
    if data.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        return data.decode("utf-32")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    return data.decode("utf-8-sig")


def _extract_pdf(data: bytes) -> list[ExtractedSection]:
    reader = PdfReader(BytesIO(data))
    if reader.is_encrypted and not reader.decrypt(""):
        raise ValueError("Could not read password-protected PDF; export an unlocked copy.")
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
    buffer_start: int | None = None

    def flush(locator: str) -> None:
        nonlocal buffer_start
        if buffered:
            sections.append(
                ExtractedSection(
                    text="\n".join(buffered),
                    heading=heading,
                    locator=f"paragraph {buffer_start}" if buffer_start else locator,
                )
            )
            buffered.clear()
            buffer_start = None

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
                if buffer_start is None:
                    buffer_start = paragraph_number
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
    fence: str | None = None

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
        fence_match = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if fence_match:
            marker = fence_match.group(1)
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
            buffered.append(line)
            continue
        if fence is not None:
            buffered.append(line)
            continue
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
    for index, row in enumerate(rows[1:], start=2):
        if not any(cell.strip() for cell in row):
            continue
        rendered = [
            f"{header[column].strip() if column < len(header) and header[column].strip() else f'Column {column + 1}'}: {value}"
            for column, value in enumerate(row)
        ]
        sections.append(
            ExtractedSection(
                text="; ".join(rendered),
                heading="CSV row",
                locator=f"row {index}",
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
    if isinstance(value, dict) and any(isinstance(item, (dict, list)) for item in value.values()):
        # Keep top-level keys attached to their values; large unrelated objects
        # should not dilute small command/configuration sections.
        return [ExtractedSection(
            text=json.dumps({key: item}, ensure_ascii=False, indent=2, sort_keys=True),
            heading=f"JSON > {key}", locator=f"JSON key {key}",
        ) for key, item in value.items()]
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
