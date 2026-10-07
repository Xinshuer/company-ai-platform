"""decide(READ) and RLS agree on labelled rows (architecture §5.1), checked against real Postgres.

Every labelled resource of cases.yaml plus random labels go into a scratch table with the
standard policy `USING (core.can_read(level, acl_groups))`. For each fabricated persona, the rows
RLS returns must equal the rows decide() allows. The only allowed difference is the explicit
defence-in-depth deny for ops roles, where policy is narrower than RLS (never wider).
"""

from __future__ import annotations

import random

import pytest

from corpai.db import pg as pgmod
from corpai.policy import LABELLED_KINDS, decide
from corpai.policy.rules import OPS_ROLES
from corpai.types import Action, Label, Level, Resource
from tests.policy.casedata import GROUP_POOL, RESOURCES
from tests.policy.fake_personas import VARIANTS, all_personas

pytestmark = pytest.mark.security


@pytest.fixture(scope="module")
def labelled_rows(pg):
    rows = {name: r for name, r in RESOURCES.items() if r.kind in LABELLED_KINDS}
    rng = random.Random(3)
    for i in range(150):
        label = Label(level=Level(rng.randint(1, 4)), acl_groups=frozenset(rng.sample(GROUP_POOL, rng.randint(0, 4))))
        rows[f"rand{i}"] = Resource(kind="doc", id=f"rand{i}", label=label)
    with pgmod.connect_owner() as c:
        c.execute("CREATE SCHEMA IF NOT EXISTS a3test")
        c.execute("DROP TABLE IF EXISTS a3test.rows")
        c.execute("CREATE TABLE a3test.rows (id text PRIMARY KEY, level smallint NOT NULL, acl_groups text[] NOT NULL)")
        c.execute("ALTER TABLE a3test.rows ENABLE ROW LEVEL SECURITY")
        c.execute("CREATE POLICY rows_read ON a3test.rows FOR SELECT TO corpai_app "
                  "USING (core.can_read(level, acl_groups))")
        c.execute("GRANT USAGE ON SCHEMA a3test TO corpai_app")
        c.execute("GRANT SELECT ON a3test.rows TO corpai_app")
        for name, r in rows.items():
            c.execute("INSERT INTO a3test.rows VALUES (%s, %s, %s)",
                      (name, int(r.label.level), sorted(r.label.acl_groups)))
    yield rows
    with pgmod.connect_owner() as c:
        c.execute("DROP SCHEMA a3test CASCADE")


PEOPLE = {**all_personas(), **VARIANTS}


@pytest.mark.parametrize("who", sorted(PEOPLE))
def test_decide_matches_rls(labelled_rows, who):
    p = PEOPLE[who]
    with pgmod.principal_tx_sync(p) as conn:
        rls = {r["id"] for r in conn.execute("SELECT id FROM a3test.rows").fetchall()}
    policy = {name for name, r in labelled_rows.items() if decide(p, Action.READ, r).allow}
    assert policy <= rls, f"policy allows rows RLS hides: {sorted(policy - rls)}"
    if p.role in OPS_ROLES:
        # narrower on purpose: L3+ rows are denied by rule even if RLS would show them
        assert all(labelled_rows[n].label.level >= Level.L3 for n in rls - policy)
    else:
        assert policy == rls, f"RLS shows rows policy denies: {sorted(rls - policy)}"
