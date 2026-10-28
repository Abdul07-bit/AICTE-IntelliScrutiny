"""Clause-aware chunking for regulatory text; LangChain's recursive splitter is used as a
secondary splitter for long clauses and for evidence text."""
from __future__ import annotations

import re
from dataclasses import dataclass

from langchain_text_splitters import RecursiveCharacterTextSplitter

# Clause headings such as "S-2.4 Fire Safety", "Clause 3.1.2 ...", "2.4 Title"
CLAUSE_RE = re.compile(r"^(?:Clause\s+)?(?P<ref>(?:[A-Z]{1,3}-)?\d+(?:\.\d+){0,3})\s+(?P<title>[A-Z][^\n]{2,120})$")
CHAPTER_RE = re.compile(r"^(?:CHAPTER|Chapter|PART|Part)\s+[\w-]+[:.\s-]+(?P<title>.+)$")

_secondary = RecursiveCharacterTextSplitter(chunk_size=1100, chunk_overlap=120,
                                            separators=["\n\n", "\n", ". ", " "])


@dataclass
class Chunk:
    text: str
    page_no: int | None
    clause_ref: str | None
    heading: str | None


def chunk_regulatory(pages: list[tuple[int, str]]) -> list[Chunk]:
    chunks: list[Chunk] = []
    cur_ref = cur_head = None
    cur_lines: list[str] = []
    cur_page: int | None = None
    chapter: str | None = None

    def flush():
        body = "\n".join(cur_lines).strip()
        if not body:
            return
        heading = f"{chapter} › {cur_head}" if chapter and cur_head else (cur_head or chapter)
        for piece in _secondary.split_text(body) if len(body) > 1100 else [body]:
            chunks.append(Chunk(piece, cur_page, cur_ref, heading))

    for page_no, text in pages:
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            ch = CHAPTER_RE.match(line)
            if ch:
                flush(); cur_lines = []; cur_ref = cur_head = None
                chapter, cur_page = ch.group("title").strip(), page_no
                continue
            m = CLAUSE_RE.match(line)
            if m:
                flush()
                cur_ref, cur_head, cur_page = m.group("ref"), m.group("title").strip(), page_no
                cur_lines = [line]
                continue
            if cur_page is None:
                cur_page = page_no
            cur_lines.append(line)
    flush()
    return chunks


def chunk_evidence(pages: list[tuple[int, str]]) -> list[Chunk]:
    out: list[Chunk] = []
    for page_no, text in pages:
        if not text.strip():
            continue
        for piece in _secondary.split_text(text):
            out.append(Chunk(piece, page_no, None, None))
    return out
