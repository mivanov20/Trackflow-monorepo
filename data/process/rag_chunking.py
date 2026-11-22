"""Reads the source documents and splits them into semantic chunks (no I/O to Qdrant or to any model here).

The four TrackFlow documents are short (about 1 KB) and have a single `#` heading each, so splitting by heading would give one
chunk per document, and a fixed number of characters would cut rules in half. The unit used instead is the **block**: the text
between blank lines, which in these documents is exactly one idea (a service list, the 90% commitment, the high-demand warning,
the long-term fee...). Two rules keep a chunk self-contained:

* A block that ends with ":" is an introduction to a list; it is joined with the block that follows.
* A list never separates from its items, and a numbered process (steps 1-3) stays whole.

Each chunk also gets a `section`: the heading of the document plus a short subtitle (`rag_config.SECTION_TITLES`, because these
documents have no subheadings). A chunk with no entry gets a label derived from its own text, so a new document still chunks.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from data.process import rag_config as config

POINT_NAMESPACE = uuid.UUID("5d0c1f0e-7a63-4b8e-9d86-3a9c6f2b1e47")  # fixed: the same chunk always gets the same point id
LABEL_MAX_CHARS = 70


@dataclass(frozen=True)
class Chunk:
    source_document: str
    section: str
    chunk_index: int
    text: str
    document_title: str

    @property
    def point_id(self) -> str:
        """Deterministic id (uuid5 of document + position): re-running setup() overwrites points instead of duplicating them."""
        return str(uuid.uuid5(POINT_NAMESPACE, f"{config.COMPANY}/{self.source_document}/{self.chunk_index}"))

    @property
    def embedding_text(self) -> str:
        """What is embedded: the title and section give the short chunk the context it lost when it was cut out of its document."""
        return f"{self.document_title} - {self.section}\n{self.text}"

    def payload(self) -> dict:
        return {
            "company": config.COMPANY,
            "source_document": self.source_document,
            "section": self.section,
            "language": config.LANGUAGE,
            "chunk_index": self.chunk_index,
            "text": self.text,
        }


def parse_blocks(markdown: str) -> tuple[str, list[str]]:
    """Returns (title, blocks). The title is the first `# heading`; blocks are the paragraphs / lists after it, joined into ideas."""
    text = markdown.replace("\r\n", "\n").strip()
    title = ""
    match = re.match(r"#\s+(.+)\n", text + "\n")
    if match:
        title = match.group(1).strip()
        text = text[match.end() :]
    raw = [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]
    blocks: list[str] = []
    carry = ""
    for block in raw:
        block = _unwrap(block)
        if carry:
            block = f"{carry}\n{block}"
            carry = ""
        if block.rstrip().endswith(":") and "\n" not in block:  # an introduction: it belongs with what follows
            carry = block
            continue
        blocks.append(block)
    if carry:
        blocks.append(carry)
    return title, blocks


def _unwrap(block: str) -> str:
    """Joins the hard-wrapped lines of a paragraph or of one list item; keeps one list item per line."""
    lines, out = block.split("\n"), []
    for line in lines:
        starts_item = re.match(r"\s*(?:[-*]|\d+\.)\s", line) is not None
        looks_like_new_line = starts_item or not out
        if looks_like_new_line:
            out.append(line.strip())
        else:
            out[-1] = f"{out[-1]} {line.strip()}"
    return "\n".join(out)


def section_label(block: str) -> str:
    """A short, readable label for a chunk, taken from its own text."""
    first_line = block.split("\n")[0].strip()
    first_line = re.sub(r"^(?:[-*]|\d+\.)\s+", "", first_line)
    before_colon = first_line.split(":")[0]
    if ":" in first_line and len(before_colon) <= LABEL_MAX_CHARS:
        label = before_colon
    else:
        label = re.split(r"(?<=[.!?])\s", first_line)[0].rstrip(".:")
    if len(label) > LABEL_MAX_CHARS:
        label = label[: LABEL_MAX_CHARS - 1].rsplit(" ", 1)[0] + "…"
    return label


def chunk_document(markdown: str, source_document: str) -> list[Chunk]:
    title, blocks = parse_blocks(markdown)
    chunks = []
    for index, block in enumerate(blocks):
        subtitle = config.SECTION_TITLES.get((source_document, index)) or section_label(block)
        chunks.append(Chunk(source_document=source_document, section=f"{title} > {subtitle}" if title else subtitle, chunk_index=index, text=block, document_title=title))
    return chunks


def load_chunks(directory: Path | None = None) -> list[Chunk]:
    """Reads every source document of CONTEXT from `docs/company-knowledge-base/` and returns all their chunks, in order."""
    directory = Path(directory or config.KNOWLEDGE_BASE_DIR)
    chunks: list[Chunk] = []
    for file_name, source_document in config.SOURCE_DOCUMENTS.items():
        path = directory / file_name
        if not path.is_file():
            raise FileNotFoundError(f"source document missing: {path} (CONTEXT-company.md lists it)")
        document_chunks = chunk_document(path.read_text(encoding="utf-8"), source_document)
        if len(document_chunks) < config.MIN_CHUNKS_PER_DOCUMENT:
            raise ValueError(f"{file_name} produced {len(document_chunks)} chunks, CONTEXT requires at least {config.MIN_CHUNKS_PER_DOCUMENT}")
        chunks.extend(document_chunks)
    return chunks
