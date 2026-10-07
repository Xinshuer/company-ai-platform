"""Split markdown into retrieval chunks: by heading, packed up to ~800 chars, long sections windowed.

Sections are kept whole where possible, because a chunk that starts in the middle of a section loses
the heading that says what it is about. Small consecutive sections are packed together (the synth
documents have many short sections). A section longer than the target is cut into windows with a
120-character overlap, at paragraph, line, sentence or word boundaries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

TARGET = 800
OVERLAP = 120
CHUNKER = "chunk-v1"  # bump when the algorithm changes, so ingestion re-chunks existing documents

_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t#]*$")


@dataclass(frozen=True)
class Chunk:
    heading: str  # heading path, e.g. "MSA C-0001 > 5. Liability"; for packed sections their common parent
    text: str


@dataclass
class _Section:
    path: str
    text: str


def _sections(body: str) -> list[_Section]:
    stack: list[tuple[int, str]] = []
    out: list[_Section] = []
    cur_path, cur_lines = "", []
    has_body = False  # a heading with no text yet is carried into the next section, not emitted alone
    in_fence = False
    for line in body.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence  # a "#" inside a code block is not a heading
        m = None if in_fence else _HEADING.match(line)
        if m:
            if has_body:
                out.append(_Section(cur_path, "\n".join(cur_lines).strip()))
                cur_lines = []
            depth, title = len(m.group(1)), m.group(2).strip()
            stack = [(d, t) for d, t in stack if d < depth] + [(depth, title)]
            cur_path = " > ".join(t for _, t in stack)
            cur_lines.append(line)
            has_body = False
        else:
            cur_lines.append(line)
            has_body = has_body or bool(line.strip())
    if "".join(cur_lines).strip():
        out.append(_Section(cur_path, "\n".join(cur_lines).strip()))
    return out


def _break_at(text: str, start: int, end: int) -> int:
    """Best cut position in text[start:end], preferring the later half of the window."""
    lo = start + (end - start) // 2
    for sep in ("\n\n", "\n", ". ", "; ", ", ", " "):
        pos = text.rfind(sep, lo, end)
        if pos != -1:
            return pos + len(sep)
    return end


def _windows(text: str, size: int, overlap: int) -> list[str]:
    out: list[str] = []
    start, n = 0, len(text)
    while start < n:
        end = n if n - start <= size else _break_at(text, start, start + size)
        piece = text[start:end].strip()
        if piece:
            out.append(piece)
        if end >= n:
            break
        nxt = max(end - overlap, start + 1)
        # start the overlap on a word boundary, not in the middle of a word
        space = text.find(" ", nxt, end)
        start = space + 1 if space != -1 else nxt
    return out


def _common_path(paths: list[str]) -> str:
    parts = [p.split(" > ") if p else [] for p in paths]
    common: list[str] = []
    for level in zip(*parts):
        if any(x != level[0] for x in level):
            break
        common.append(level[0])
    return " > ".join(common) if common else paths[0]


def chunk_markdown(body: str, *, size: int = TARGET, overlap: int = OVERLAP) -> list[Chunk]:
    if overlap >= size:
        raise ValueError("overlap must be smaller than the chunk size")
    chunks: list[Chunk] = []
    pack: list[_Section] = []

    def flush() -> None:
        if pack:
            chunks.append(Chunk(_common_path([s.path for s in pack]), "\n\n".join(s.text for s in pack)))
            pack.clear()

    for sec in _sections(body):
        if len(sec.text) > size:
            flush()
            chunks.extend(Chunk(sec.path, w) for w in _windows(sec.text, size, overlap))
            continue
        if pack and sum(len(s.text) + 2 for s in pack) + len(sec.text) > size:
            flush()
        pack.append(sec)
    flush()
    return chunks
