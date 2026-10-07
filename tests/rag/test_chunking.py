"""Markdown chunking: by heading, packed to ~800 chars, long sections windowed with 120 overlap."""

from __future__ import annotations

import pytest

from corpai.rag import chunk_markdown, split_front_matter


def test_small_sections_are_packed_with_their_headings():
    body = "# Handbook\n\n## Holidays\n30 days.\n\n## Sick leave\nTell your manager.\n"
    chunks = chunk_markdown(body)
    assert len(chunks) == 1
    assert chunks[0].heading == "Handbook"
    assert "## Sick leave" in chunks[0].text and "30 days." in chunks[0].text


def test_sections_split_when_the_pack_is_full():
    sec = "word " * 100  # ~500 chars
    body = f"# Doc\n\n## A\n{sec}\n\n## B\n{sec}\n\n## C\n{sec}\n"
    chunks = chunk_markdown(body)
    # "# Doc" alone has no text, so it is not a section; two ~500-char sections do not fit in one chunk
    assert [c.heading for c in chunks] == ["Doc > A", "Doc > B", "Doc > C"]
    assert all(len(c.text) <= 800 for c in chunks)


def test_long_section_is_windowed_with_overlap():
    words = [f"w{i:04d}" for i in range(600)]  # ~3000 chars
    chunks = chunk_markdown("## Long\n" + " ".join(words))
    assert len(chunks) >= 4
    assert all(len(c.text) <= 800 for c in chunks)
    assert all(c.heading == "Long" for c in chunks)
    for a, b in zip(chunks, chunks[1:]):
        assert b.text.split()[0] in a.text.split(), "consecutive windows must overlap"
    # nothing lost: every word appears in some chunk, and no word is cut in half
    seen = set(" ".join(c.text for c in chunks).split())
    assert set(words) <= seen | {"## Long"}


def test_heading_path_and_code_fences():
    body = "# T\n## A\ntext a\n### A1\ntext a1\n```\n# not a heading\n```\n## B\ntext b\n"
    chunks = chunk_markdown(body, size=20, overlap=5)
    headings = [c.heading for c in chunks]
    assert "T > A > A1" in headings and "T > B" in headings
    assert not any(h.endswith("not a heading") for h in headings)


def test_empty_and_bad_arguments():
    assert chunk_markdown("") == [] and chunk_markdown("\n\n  \n") == []
    with pytest.raises(ValueError):
        chunk_markdown("x", size=100, overlap=100)


def test_front_matter_split():
    meta, body = split_front_matter("---\ndoc_id: a\nlevel: 2\n---\n\n# Title\nbody\n")
    assert meta == {"doc_id": "a", "level": 2} and body.startswith("# Title")
    for bad in ("no front matter", "---\nunterminated: 1\n", "---\n- a list\n---\nbody"):
        with pytest.raises(ValueError):
            split_front_matter(bad)
