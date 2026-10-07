"""The vendored Clause Check: unchanged where it matters, no provider SDKs, no env/.env reading."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest

from corpai.legal.clausecheck import Settings
from corpai.legal.clausecheck.split import contract_from_text

PKG = Path(__file__).resolve().parents[2] / "corpai" / "legal" / "clausecheck"
LEGAL = PKG.parent

# sha256 of the source segments at Xinshuer/Contract-Clause-Reviewer@040e039 (clausecheck/review.py and files).
ORIGINAL_SEGMENTS = {
    "OpenAICompatBackend": "7759098caf3add18808f0037b06d9518f17c16b515baeb701bddb0288a4ea26e",
    "model_review": "e2a89005a10c52d73f994595162a0354cc762094068178b31f614acb2c0345f1",
    "_finalise_redline": "e49b27ac59d0ab39dae94030a1ebef413eb821f13224310c9dec174094f0a2a5",
    "rule_based_review": "590ef48540fad8dec6f8daecdc700bf4154184b91a48452f7d0261b0fe1f6898",
}
ORIGINAL_FILES = {
    "tools.py": "cd5d1032423e70948f3b37a822562d4db467f76e25e291f83d069c01099f9381",
    "playbook.py": "8dd74b15f4ff7bd74b87780ddd12e3694979a860e9343eec48a9d1fa32556280",
    "redline.py": "805728c34d80a05e677755ce74dcdcafc7acd4f41d22470d7019298cdf6c2cea",
    "parse.py": "675219e3cbb2509a7f0df49b3e5a0e92aec4b3f09c88a5e8238f0eb3406edd18",
    "pipeline.py": "2d09dd7a25e50260b2580b222e5d5856951d936bab640ab60dfcaf582cf24e33",
}


def _segment(path: Path, name: str) -> str:
    src = path.read_text(encoding="utf-8")
    for node in ast.parse(src).body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name == name:
            return ast.get_source_segment(src, node) or ""
    raise AssertionError(f"{name} not found")


@pytest.mark.parametrize("name", sorted(ORIGINAL_SEGMENTS))
def test_review_loop_and_compat_backend_are_byte_identical_to_the_original(name):
    seg = _segment(PKG / "review.py", name)
    assert hashlib.sha256(seg.encode("utf-8")).hexdigest() == ORIGINAL_SEGMENTS[name]


@pytest.mark.parametrize("fname", sorted(ORIGINAL_FILES))
def test_unmodified_files_are_identical(fname):
    assert hashlib.sha256((PKG / fname).read_bytes()).hexdigest() == ORIGINAL_FILES[fname]


def _imports(path: Path) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.add(node.module.split(".")[0])
    return out


def test_no_provider_sdk_hosted_tracing_ui_or_sqlite_in_the_vendored_package():
    banned = {"anthropic", "langfuse", "streamlit", "dotenv", "sqlite3", "langgraph"}
    for path in PKG.glob("*.py"):
        assert not (_imports(path) & banned), path.name
    for gone in ("graph.py", "api.py", "cli.py"):
        assert not (PKG / gone).exists()


def test_settings_read_nothing_from_the_environment(monkeypatch):
    monkeypatch.setenv("CLAUSECHECK_MODE", "mock")
    monkeypatch.setenv("CLAUSECHECK_LOCAL_API_KEY", "sk-should-not-be-read")
    s = Settings()
    assert s.mode == "gateway" and s.compat_api_key == "" and s.local_api_key == ""
    assert "deepseek" not in {f.lower() for f in Settings.__dataclass_fields__}
    assert "sk-" not in repr(Settings(mode="local", local_api_key="sk-secret-token"))
    assert not (_imports(PKG / "config.py") & {"os", "dotenv"})


def test_split_accepts_markdown_headings_and_keeps_numbered_ones():
    c = contract_from_text("MSA C-1\n\nBetween A and B.\n\n## 5. Liability\nCapped.\n\n8.1 Cap on Liability\n"
                           "Subject to Section 12, capped.\n\n12 Exclusions\nNone.\n")
    assert [(x.clause_id, x.heading) for x in c.clauses] == [("5", "Liability"), ("8.1", "Cap on Liability"),
                                                           ("12", "Exclusions")]
    assert c.get("8.1").refs == ["12"]


def test_notice_names_the_source_commit_and_changes():
    notice = (PKG / "NOTICE.md").read_text(encoding="utf-8")
    assert "Xinshuer/Contract-Clause-Reviewer" in notice and "040e039" in notice
    for f in ("split.py", "review.py", "config.py", "tracing.py"):
        assert f in notice


def test_no_ai_attribution_text_in_the_stream_files():
    root = LEGAL.parents[1]
    files = [*LEGAL.rglob("*.py"), *LEGAL.rglob("*.md"), *LEGAL.rglob("*.json"),
             *(root / "tests" / "legal").glob("*.py"), *(root / "evals" / "legal").glob("*"),
             *(root / "migrations").glob("08*.sql")]
    needles = ["Co-" + "Authored-By", "Generated " + "with", "noreply@" + "anthropic", "Clau" + "de Code"]
    for f in files:
        if f.is_file():
            text = f.read_text(encoding="utf-8", errors="replace")
            assert not any(n in text for n in needles), f


ORIGINAL_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "clausecheck_original"


def test_original_mock_pipeline_behaves_the_same_with_the_original_playbook():
    """The original repo's test_pipeline_mock, against the vendored package and the original customer-side
    playbook (copied from Xinshuer/Contract-Clause-Reviewer data/): the vendored loop decides as before."""
    from corpai.legal.clausecheck.models import ReviewReport
    from corpai.legal.clausecheck.pipeline import review_contract
    from corpai.legal.clausecheck.playbook import Playbook

    report = review_contract(ORIGINAL_FIXTURES / "sample_contract.txt", Settings(mode="mock"),
                             Playbook.load(ORIGINAL_FIXTURES / "playbook.json"))
    by_id = {r.clause.clause_id: r for r in report.reviews}
    assert "8" not in by_id and "8.1" in by_id
    assert by_id["8.1"].verdict.verdict != "accept"  # the cross-reference trap
    assert "12" in by_id["8.1"].verdict.consulted_clauses
    assert by_id["4.2"].verdict.verdict == "redline" and by_id["4.2"].redline.located
    assert by_id["10.1"].verdict.topic == "data_processing"
    assert by_id["14"].verdict.verdict == "accept"
    assert by_id["2.2"].verdict.verdict == "accept"
    ReviewReport.model_validate_json(report.model_dump_json())


def test_vendored_settings_have_no_default_playbook():
    """Review finding: the default used to resolve to ABC Tech's provider-side playbook, so the
    unchanged pipeline silently applied different rules (the 8.1 trap was accepted)."""
    from corpai.legal.clausecheck.pipeline import review_contract

    assert Settings().playbook_path is None
    with pytest.raises((TypeError, ValueError, OSError)):
        review_contract(ORIGINAL_FIXTURES / "sample_contract.txt", Settings(mode="mock"))
