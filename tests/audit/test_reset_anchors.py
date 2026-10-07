"""`db reset` moves the audit anchor file aside (issues-log WI-02).

The anchors pin the head of a chain that a reset deletes; left in place they make verify_chain
report the fresh chain as tampered although its links are intact.
"""

from __future__ import annotations

from corpai.audit.anchor import ANCHOR_FILE, read_anchors
from corpai.cli.db import archive_anchors


def test_reset_moves_the_old_anchors_aside_and_keeps_them(tmp_path):
    src = tmp_path / ANCHOR_FILE
    src.write_text('{"seq": 1}\n', encoding="utf-8")
    dst = archive_anchors(tmp_path)
    assert dst is not None and dst.parent == tmp_path and dst.name.startswith("audit_anchors.before-reset-")
    assert not src.exists() and dst.read_text(encoding="utf-8") == '{"seq": 1}\n'
    # the new chain starts without anchors: nothing stale to compare against
    assert read_anchors(src) == []


def test_reset_without_anchor_file_is_a_no_op(tmp_path):
    assert archive_anchors(tmp_path) is None
