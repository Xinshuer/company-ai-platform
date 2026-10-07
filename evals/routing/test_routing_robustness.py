"""Routing robustness eval (marker `eval`): the 四.3 samples that are confidential by their text alone
must stay local when the text is disguised the way a careless paste or a deliberate bypass does it
(design-review 四.2 rule 4 "拿不准按更高的级别处理"; issues-log B-03, R-04).

Routing is code, so the result does not depend on the model: fake and live runs give the same
numbers. Each variant must hold for every sample, except the pairs in KNOWN_GAPS (found by this eval,
reported as change requests to corpai/classify); the per-variant pass rates are printed.
"""

from __future__ import annotations

import pytest

from corpai.classify import load_gazetteer_from_db
from corpai.gateway import FakeLLM, ModelGateway, load_config
from evals.routing.samples import SAMPLES, ask, fill, holds

pytestmark = [pytest.mark.eval, pytest.mark.anyio]

ZWSP, SHY, NBSP = "​", "­", " "


def _every_nth(text: str, mark: str, n: int = 3) -> str:
    return "".join(ch + mark if (i % n == n - 1 and ch.isalnum()) else ch for i, ch in enumerate(text))


def _fullwidth(text: str) -> str:
    # ASCII letters and digits to their fullwidth forms (NFKC folds them back)
    return "".join(chr(ord(c) + 0xFEE0) if c.isascii() and c.isalnum() else c for c in text)


VARIANTS = {
    "plain": lambda t: t,
    "zero_width": lambda t: _every_nth(t, ZWSP),
    "soft_hyphen": lambda t: _every_nth(t, SHY, 4),
    "nbsp": lambda t: t.replace(" ", NBSP),
    "fullwidth": _fullwidth,
    "wrapped": lambda t: f"Please help with the following.\n\n> {t}\n\nThanks!",
}
# (sample id, variant) -> why it does not hold today. Empty since the wiring integration fixed the
# three gaps this eval found (issues-log WI): the normalised pass now keeps a finding it sees at a
# higher level than the raw pass, and no-break spaces are normalised before the phone patterns.
KNOWN_GAPS: dict[tuple[str, str], str] = {}
# confidential by text alone: no attached source or playbook decides the level for them
TEXT_ONLY = [s for s in SAMPLES if s["route"] == "local" and not s.get("sources") and not s.get("playbook")
             and not s.get("known_gap")]
_results: dict[str, list[bool]] = {v: [] for v in VARIANTS}


@pytest.fixture
def gateway(seeded) -> ModelGateway:
    load_gazetteer_from_db()
    return ModelGateway(FakeLLM(), config=load_config())


def test_there_are_enough_text_only_samples():
    assert len(TEXT_ONLY) >= 10


@pytest.mark.parametrize("variant", sorted(VARIANTS))
async def test_disguised_confidential_text_stays_local(variant, gateway, personas):
    failures, known = [], []
    for sample in TEXT_ONLY:
        text = VARIANTS[variant](fill(sample["text"]))
        res = await ask(gateway, personas[sample["persona"]], text)
        ok = holds(sample, res.route, res.level)
        _results[variant].append(ok)
        if not ok:
            gap = KNOWN_GAPS.get((sample["id"], variant))
            (known if gap else failures).append(f"{sample['id']}: {res.route} L{int(res.level)}"
                                                + (f" ({gap})" if gap else ""))
    rate = sum(_results[variant]) / max(1, len(_results[variant]))
    print(f"routing robustness {variant}: {rate:.0%} of {len(TEXT_ONLY)} samples stay local")
    assert not failures, failures
    if known:
        pytest.xfail("; ".join(known))
