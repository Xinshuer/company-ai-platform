"""decide() and RLS agree by construction on real seeded rows (architecture §5.1, §6).

test_permission_matrix.py checks decide() on the design-review 五 cases with fabricated personas. This
file is the RLS side: for every distinct label shape synth writes into each business table, the
policy decision for a persona must equal what RLS shows that persona for the same row.
"""

from __future__ import annotations

import pytest

from corpai.policy import LABELLED_KINDS, decide
from corpai.synth.personas import PERSONAS, SERVICE_ACCOUNTS
from corpai.synth.writer import BUSINESS_TABLES
from corpai.types import Action, Resource
from tests.integration.rows import TABLE_KIND, owner_rows, pk_columns, resource_of, visible_rows

pytestmark = pytest.mark.security

READ_ACTIONS = (Action.READ, Action.SEARCH, Action.EXPORT)


@pytest.fixture(scope="module")
def samples(seeded) -> dict[str, tuple[list[str], list[tuple[tuple, Resource]]]]:
    """table -> (pk columns, one (key, Resource) per distinct label shape in that table)."""
    out = {}
    for t in BUSINESS_TABLES:
        pk = pk_columns(t)
        assert pk, f"{t} has no primary key"
        by_label: dict[tuple, tuple[tuple, Resource]] = {}
        for row in owner_rows(f"SELECT * FROM {t} ORDER BY {', '.join(pk)}"):
            shape = (row["level"], tuple(sorted(row["acl_groups"])), row.get("dept"), row.get("owner_id"),
                     row.get("trust"))
            if shape not in by_label:
                key = tuple(row[c] for c in pk)
                by_label[shape] = (key, resource_of(t, row, key))
        out[t] = (pk, list(by_label.values()))
    return out


@pytest.fixture(scope="module")
def principals(personas):
    from corpai.identity import directory

    out = dict(personas)
    for svc in SERVICE_ACCOUNTS:
        out[svc] = directory.get_principal_sync(svc)
    return out


def test_every_table_maps_to_a_labelled_kind():
    assert set(TABLE_KIND) == set(BUSINESS_TABLES)
    assert set(TABLE_KIND.values()) <= LABELLED_KINDS


def test_samples_cover_every_seeded_table(samples):
    empty = [t for t, (_, items) in samples.items() if not items]
    assert empty == ["hr.screenings"]  # the seed has no screening decisions yet (M4 HR writes them)
    assert sum(len(items) for _, items in samples.values()) >= 50


@pytest.mark.parametrize("key", sorted(PERSONAS) + list(SERVICE_ACCOUNTS))
def test_decide_equals_rls_for_seeded_rows(principals, samples, key):
    p = principals[key]
    assert p is not None, key
    visible = visible_rows(p, BUSINESS_TABLES)
    mismatches = []
    allowed = 0
    for t, (pk, items) in samples.items():
        seen = {tuple(r[c] for c in pk) for r in visible[t]}
        for row_key, res in items:
            rls = row_key in seen
            for action in READ_ACTIONS:
                d = decide(p, action, res)
                if d.allow is not rls:
                    mismatches.append((t, row_key, action.value, f"rls={rls}", d.rule_id, d.reason))
            allowed += rls
    assert mismatches == []
    if key in PERSONAS:
        assert allowed > 0, "every active human reads at least the public regulation library"


def test_a_grant_opens_the_same_row_in_decide_and_rls(principals, samples):
    """§5.1 ticket-scoped access: grant:crm:<account> makes another rep's account readable, in both layers."""
    from dataclasses import replace

    p = principals["sales_staff"]
    _, items = samples["crm.accounts"]
    other = next((k, r) for k, r in items if r.label.owner_id != p.user_id)
    (account_id,), res = other
    q = replace(p, groups=p.groups | {f"grant:crm:{account_id}"})
    for who, expect in ((p, False), (q, True)):
        rls = account_id in {r["account_id"] for r in visible_rows(who, ["crm.accounts"])["crm.accounts"]}
        assert rls is expect and decide(who, Action.READ, res).allow is expect
