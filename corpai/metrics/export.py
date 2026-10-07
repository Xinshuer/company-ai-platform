"""Watermarked report export (design-review 二.7: "导出件加用户 id 和时间水印并留审计").

The file is Markdown. A visible watermark line names who exported it, when (UTC wall clock) and from
which run, at the top and again at the bottom so a cropped copy still carries it. A footer HTML comment
holds a SHA-256 over the watermark fields and the body, so an edited copy can be told from the original
(verify_export). Plain ASCII/UTF-8 only: no zero-width characters, nothing hidden that a copy-paste
would silently drop or a reviewer could not see in the raw file.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from corpai import audit
from corpai.classify import classify_text
from corpai.clock import now_utc
from corpai.policy import decide
from corpai.types import Action, Label, Level, Principal, Resource, Role

_FOOTER_RE = re.compile(r"<!-- corpai-export v1 sha256=([0-9a-f]{64}) -->\s*$")
_SAFE_RE = re.compile(r"[^a-z0-9]+")
_RUN_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
LEVEL_NAMES = {1: "L1 public", 2: "L2 internal", 3: "L3 confidential", 4: "L4 restricted"}


@dataclass(frozen=True)
class ExportCheck:
    ok: bool
    sha256: str | None
    reason: str


def _digest(user_id: str, name: str, ts: str, run_id: str, level: int, title: str, body: str) -> str:
    h = hashlib.sha256()
    for part in ("corpai.export.v1", user_id, name, ts, run_id, str(level), title, body):
        b = part.encode("utf-8")
        h.update(len(b).to_bytes(8, "big") + b)  # length-prefixed: no field can bleed into the next
    return h.hexdigest()


def _filename(title: str, run_id: str, ts_compact: str) -> str:
    slug = _SAFE_RE.sub("-", title.lower()).strip("-")[:48] or "report"
    return f"{slug}-{run_id}-{ts_compact}.md"


def _prepare(principal: Principal, title: str, body_md: str, run_id: str, level: Level | int,
             domain: str, report_level: Level | int | None) -> tuple[int, object]:
    """Validation and the policy check. Returns (level, denial or None).

    The policy check always runs (fail closed): EXPORT is READ plus the watermark obligation, so a
    report of a domain the caller may not read is never exported. The watermark level is never lower
    than the report's own hwm (`report_level`, e.g. ReportDraft.level) or what the text itself
    classifies as, so a caller cannot label an L3/L4 report "L1 public" by passing level=1."""
    if not principal.active or principal.role == Role.SERVICE:
        raise PermissionError("only active people can export reports")
    if not isinstance(run_id, str) or not _RUN_RE.match(run_id):
        raise ValueError("run_id must be a short id like run_...")
    if not isinstance(title, str) or not title.strip() or not isinstance(body_md, str):
        raise ValueError("title and body are required")
    if not isinstance(domain, str) or not domain.strip():
        raise ValueError("domain is required (the report's domain decides who may export it)")
    floor = [int(Level(int(level))), int(classify_text(f"{title}\n{body_md}").level)]
    if report_level is not None:
        floor.append(int(Level(int(report_level))))
    lvl = max(floor)
    d = decide(principal, Action.EXPORT,
               Resource(kind="report", id=run_id, label=Label(level=Level(lvl)), attrs={"domain": domain}))
    if not d.allow:
        return lvl, d
    return lvl, None


def _build(principal: Principal, title: str, body_md: str, run_id: str, lvl: int) -> tuple[bytes, str, str]:
    now = now_utc().replace(microsecond=0)
    ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    title_line = " ".join(title.split())
    body = body_md.replace("\r\n", "\n").rstrip("\n")
    mark = (f"WATERMARK | exported by {principal.user_id} {principal.name} | {ts} UTC | run {run_id} | "
            f"{LEVEL_NAMES[lvl]} | do not forward")
    digest = _digest(principal.user_id, principal.name, ts, run_id, lvl, title_line, body)
    text = (f"> {mark}\n\n# {title_line}\n\n{body}\n\n---\n\n> {mark}\n"
            f"<!-- corpai-export v1 sha256={digest} -->\n")
    return text.encode("utf-8"), _filename(title_line, run_id, now.strftime("%Y%m%dT%H%M%SZ")), digest


def _audit_args(principal: Principal, run_id: str, lvl: int, *, allow: bool, reason: str,
                details: dict) -> dict:
    return {"actor": principal.user_id, "action": "export_report", "resource": f"report:{run_id}",
            "decision": "allow" if allow else "deny", "reason": reason, "run_id": run_id, "level": lvl,
            "details": details}


def export_report(principal: Principal, *, title: str, body_md: str, run_id: str, domain: str,
                  level: Level | int = Level.L4, report_level: Level | int | None = None) -> tuple[bytes, str]:
    """Render a report as watermarked Markdown and audit the export. Returns (bytes, filename).

    `domain` is required: policy.decide(EXPORT, report) must allow it (CEO; a head for their own
    domain). The watermark level is max(`level` (default L4, fails safe), `report_level` (the
    report's hwm, e.g. ReportDraft.level), the text's own classification). Service accounts and
    deactivated users cannot export. Sync; use aexport_report on the event loop.
    """
    lvl, denial = _prepare(principal, title, body_md, run_id, level, domain, report_level)
    if denial is not None:
        audit.record("export", **_audit_args(principal, run_id, lvl, allow=False, reason=denial.reason,
                                             details={"rule": denial.rule_id}))
        raise PermissionError(f"export not allowed: {denial.reason}")
    data, filename, digest = _build(principal, title, body_md, run_id, lvl)
    audit.record("export", **_audit_args(principal, run_id, lvl, allow=True, reason="watermarked export",
                                         details={"filename": filename, "sha256": digest, "bytes": len(data),
                                                  "domain": domain}))
    return data, filename


async def aexport_report(principal: Principal, *, title: str, body_md: str, run_id: str, domain: str,
                         level: Level | int = Level.L4, report_level: Level | int | None = None
                         ) -> tuple[bytes, str]:
    """Async twin of export_report (audit through audit.arecord)."""
    lvl, denial = _prepare(principal, title, body_md, run_id, level, domain, report_level)
    if denial is not None:
        await audit.arecord("export", **_audit_args(principal, run_id, lvl, allow=False, reason=denial.reason,
                                                    details={"rule": denial.rule_id}))
        raise PermissionError(f"export not allowed: {denial.reason}")
    data, filename, digest = _build(principal, title, body_md, run_id, lvl)
    await audit.arecord("export", **_audit_args(principal, run_id, lvl, allow=True, reason="watermarked export",
                                                details={"filename": filename, "sha256": digest,
                                                         "bytes": len(data), "domain": domain}))
    return data, filename


def verify_export(data: bytes) -> ExportCheck:
    """Recompute the footer hash of an exported file: tells an untouched export from an edited copy."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return ExportCheck(False, None, "not UTF-8")
    m = _FOOTER_RE.search(text)
    if not m:
        return ExportCheck(False, None, "no export footer")
    head = re.match(r"> WATERMARK \| exported by (\S+) (.*?) \| (\S+) UTC \| run (\S+) \| (L[1-4]) [a-z]+ \| "
                    r"do not forward\n\n# (.*)\n\n", text)
    if not head:
        return ExportCheck(False, m.group(1), "watermark line missing or altered")
    user_id, name, ts, run_id, lvl, title = head.groups()
    rest = text[head.end():]
    tail = f"\n\n---\n\n> {text.splitlines()[0][2:]}\n"
    end = rest.rfind(tail)
    if end < 0:
        return ExportCheck(False, m.group(1), "closing watermark missing or altered")
    body = rest[:end]
    digest = _digest(user_id, name, ts, run_id, int(lvl[1]), title, body)
    if digest != m.group(1):
        return ExportCheck(False, m.group(1), "content changed after export")
    return ExportCheck(True, digest, "intact")
