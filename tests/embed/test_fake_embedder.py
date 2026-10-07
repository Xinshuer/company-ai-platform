"""FakeEmbedder: deterministic, 384-dim, unit length; related texts close, unrelated texts far."""

from __future__ import annotations

import math
import subprocess
import sys

import pytest

from corpai.config import ROOT
from corpai.embed import DIM, FakeEmbedder, to_pgvector
from corpai.embed.fake import tokens


def cos(a, b) -> float:
    return sum(x * y for x, y in zip(a, b))


@pytest.fixture(scope="module")
def emb():
    return FakeEmbedder()


def test_shape_and_unit_length(emb):
    vecs = emb.embed(["Travel policy for trains", "", "   ", "!!!", "差旅"])
    assert all(len(v) == DIM == 384 for v in vecs)
    assert all(math.isclose(math.sqrt(sum(x * x for x in v)), 1.0, rel_tol=1e-9) for v in vecs)


def test_deterministic_within_and_across_processes(emb):
    text = "Month-end close: reconcile bank and accounts receivable"
    here = emb.embed_one(text)
    assert here == emb.embed_one(text)
    # Python's str hash is salted per process; the embedder must not depend on it, or vectors stored at
    # ingestion would not match query vectors computed by the API process.
    code = ("from corpai.embed import FakeEmbedder; import json; "
            f"print(json.dumps(FakeEmbedder().embed_one({text!r})))")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True)
    import json

    assert json.loads(out.stdout) == pytest.approx(here)


def test_related_texts_are_closer_than_unrelated(emb):
    q = emb.embed_one("How many holiday days do I get per year?")
    near = emb.embed_one("Holidays: 30 days per year plus public holidays at your work location.")
    far = emb.embed_one("Each party's aggregate liability is limited to EUR 500,000.")
    assert cos(q, near) > cos(q, far) + 0.1


def test_bigrams_reward_word_order(emb):
    a = emb.embed_one("data protection officer")
    assert cos(a, emb.embed_one("the data protection officer is designated")) > \
        cos(a, emb.embed_one("officer protection of data"))


def test_cjk_characters_are_tokens():
    assert tokens("GDPR第17条") == ["gdpr", "第", "17", "条"]
    assert tokens("差旅政策") == ["差", "旅", "政", "策"]


def test_chinese_query_matches_english_only_through_shared_tokens(emb):
    """Documented limitation: the fake embedder has no meaning, only tokens. A Chinese question about the
    travel policy is unrelated to the English policy; one that shares "GDPR" and "17" is related.
    Real cross-lingual retrieval needs CORPAI_EMBED=fastembed (tests/embed/test_fastembed.py)."""
    doc = emb.embed_one("GDPR Article 17: Right to erasure. The data subject may obtain erasure of personal data.")
    travel = emb.embed_one("Travel policy: book trains for trips under 600 km.")
    no_overlap = cos(emb.embed_one("删除权是什么"), doc)
    shared = cos(emb.embed_one("GDPR 第17条 删除权是什么"), doc)
    assert abs(cos(emb.embed_one("差旅政策是什么"), travel)) < 0.1
    assert abs(no_overlap) < 0.1
    assert shared > no_overlap + 0.05


def test_to_pgvector_checks_dimensions(emb):
    text = to_pgvector(emb.embed_one("x"))
    assert text.startswith("[") and text.endswith("]") and text.count(",") == DIM - 1
    with pytest.raises(ValueError):
        to_pgvector([0.1, 0.2])
