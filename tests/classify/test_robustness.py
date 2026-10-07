"""classify_text never raises, fails closed, and stays fast (gateway hot path)."""

from __future__ import annotations

import random
import statistics
import time

import pytest

from corpai.classify import MAX_CHARS, classify_text, dlp_scan, mask_for_level, redact_protected, scrub_secrets
from corpai.classify import detectors
from corpai.types import Level, Route

_ALPHABETS = [
    (0x20, 0x7E), (0xA0, 0x17F), (0x370, 0x3FF), (0x400, 0x4FF), (0x4E00, 0x9FFF), (0x3000, 0x303F),
    (0xFF00, 0xFFEF), (0x1F300, 0x1F6FF), (0x0, 0x1F), (0xD7FF, 0xD7FF), (0x200B, 0x200F), (0x202A, 0x202E),
]
_FRAGMENTS = ["€", "+49 ", "0", "@", "```", "\n    ", "KB-CANARY-", "sk-", "eyJ", "-----BEGIN ", "Age:", "：",
              "DE89", "salary ", "年薪", "Herr ", "Anna Becker", "M-1", "kb_", "fleetpilot", "\\", "(", "[", "*"]


def _random_text(rng: random.Random, n: int) -> str:
    out: list[str] = []
    while len(out) < n:
        if rng.random() < 0.15:
            out.append(rng.choice(_FRAGMENTS))
        else:
            lo, hi = rng.choice(_ALPHABETS)
            out.append(chr(rng.randint(lo, hi)))
    return "".join(out)


def test_fuzz_never_raises():
    rng = random.Random(1234)
    for i in range(400):
        text = _random_text(rng, rng.randint(0, 600))
        c = classify_text(text)
        assert isinstance(c.level, Level)
        assert "classifier_error" not in c.kinds, (i, text)  # random text is not an internal error
        for f in c.findings:
            assert 0 <= f.start <= f.end <= len(text)
        # the other entry points survive the same inputs
        scrub_secrets(text)
        redact_protected(text)
        dlp_scan(text, route=Route.EXTERNAL)
        m = mask_for_level(text, Level.L3)
        assert m is None or isinstance(m, str)


ZWSP = chr(0x200B)
SHY = chr(0x00AD)
RLO = chr(0x202E)


def _fullwidth(s: str) -> str:
    return "".join(chr(ord(c) + 0xFEE0) if "!" <= c <= "~" else c for c in s)


@pytest.mark.parametrize("text,level,kind", [
    (f"KB-CANARY-0A1B{ZWSP}2C3D", Level.L4, "canary"),
    (f"key sk-abcdefghij{ZWSP}klmnopqrstuvwxyz123456", Level.L4, "secret"),
    (f"pass{SHY}word=hunter2", Level.L4, "secret"),
    (_fullwidth("+49 30 1234567"), Level.L3, "phone"),
    (f"mail jane{ZWSP}@example.com", Level.L3, "email"),
    (f"Anna {RLO}Becker's salary is €78,000", Level.L4, "salary"),
    (_fullwidth("DE89 3704 0044 0532 0130 00"), Level.L3, "iban"),
])
def test_hiding_characters_do_not_bypass_detection(text, level, kind):
    """Zero-width / bidi / soft-hyphen characters and fullwidth digits must not defeat DLP."""
    c = classify_text(text)
    assert c.level == level and kind in c.kinds
    assert dlp_scan(text, route=Route.EXTERNAL).blocked
    for f in c.findings:
        assert 0 <= f.start <= f.end <= len(text)


def test_hidden_findings_are_masked_in_the_original_text():
    text = f"call {_fullwidth('+49 30 1234567')} today"
    out = mask_for_level(text, Level.L3)
    assert out == "call ⟨phone⟩ today"


def test_lone_surrogates_and_controls():
    text = "abc\ud800def\x00\x1b[31m +49 30 1234567 \udfff"
    c = classify_text(text)
    assert c.level == Level.L3 and "phone" in c.kinds


@pytest.mark.parametrize("bad", [None, b"bytes", 42, ["list"], object()])
def test_non_string_input_fails_closed(bad):
    c = classify_text(bad)  # type: ignore[arg-type]
    assert c.level == Level.L4
    assert [(f.kind, f.start, f.end, f.level) for f in c.findings] == [("classifier_error", 0, 0, Level.L4)]


@pytest.mark.parametrize("floor", [0, 7, "L9", "secret", None, 1.5])
def test_bad_floor_fails_closed(floor):
    assert classify_text("hello", floor=floor).level == Level.L4  # type: ignore[arg-type]


def test_internal_error_fails_closed(monkeypatch):
    def broken(text):
        raise RuntimeError("detector bug")

    monkeypatch.setattr(detectors, "detect", broken)
    c = classify_text("What is deferred revenue?")
    assert c.level == Level.L4 and c.kinds == {"classifier_error"}
    assert dlp_scan("What is deferred revenue?", route=Route.EXTERNAL).blocked
    assert mask_for_level("What is deferred revenue?", Level.L1) is None


def test_recursion_and_memory_errors_fail_closed(monkeypatch):
    for exc in (RecursionError, MemoryError):
        def broken(text, exc=exc):
            raise exc()

        monkeypatch.setattr(detectors, "detect", broken)
        assert classify_text("x").level == Level.L4


def test_oversized_input_fails_closed():
    c = classify_text("a" * (MAX_CHARS + 1))
    assert c.level == Level.L4 and c.kinds == {"classifier_error"}


@pytest.mark.parametrize("unit", [
    "Hello world. Anna Becker paid €5,000 for FleetPilot. Call +49 30 1234567.\n",
    "a",
    "1",
    "1 ",
    "Age: ",
    "@a.",
    "0 ",
    "    x = 1\n",
    "    import kb_core\n    x = 1\n    y = 2\nkestrelbyte\n",  # many code blocks × many identifiers
    "DE89 ",
    "a" + chr(0x200B),                      # every character triggers the normalised second pass
    "１２３ 王伟的年薪 ",
])
def test_one_megabyte_in_reasonable_time(unit):
    text = (unit * (1_000_000 // len(unit) + 1))[:1_000_000]
    t0 = time.perf_counter()
    c = classify_text(text)
    elapsed = time.perf_counter() - t0
    assert "classifier_error" not in c.kinds
    assert elapsed < 10.0, f"{unit!r}: {elapsed:.1f}s"


def test_typical_4kb_message_is_fast():
    text = (
        "Hi team, quick update on the Nordhafen renewal. Anna Becker met their logistics lead on Tuesday; "
        "they want the FleetPilot telematics integration before signing. Pricing was not discussed. "
        "Jonas Weber will check the API limits with engineering and report back by Friday. "
        "Open questions: SSO, data residency in the EU, and the support SLA for weekend operations.\n"
    ) * 12
    text = text[:4096]
    classify_text(text)  # warm-up
    samples = []
    for _ in range(30):
        t0 = time.perf_counter()
        classify_text(text)
        samples.append(time.perf_counter() - t0)
    assert statistics.median(samples) < 0.010, f"median {statistics.median(samples) * 1000:.1f} ms"
