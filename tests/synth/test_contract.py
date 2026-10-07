"""The §5 effective-group table, spelled out by hand in conftest.build_principal, must equal what
identity computes for every generated user. The synth RLS tests use identity's principals; this
test keeps the hand-written contract honest instead of letting the two drift apart."""

from __future__ import annotations

from corpai.db import pg as pgmod
from corpai.identity.directory import get_principal_sync
from corpai.types import Dept, Level, Principal, Role


def build_principal(user_id: str, *, extra_groups: frozenset[str] = frozenset()) -> Principal | None:
    """The §5 group table written out by hand, from core.principal_row.

    Not used to build test principals (principal_for asks identity); kept as an explicit contract:
    the test below asserts it equals identity's effective groups for every user, so a
    change in identity or in the §5 table cannot drift silently.
    """
    with pgmod.connect_app() as conn:
        row = conn.execute("SELECT * FROM core.principal_row(%s)", (user_id,)).fetchone()
    if row is None:
        return None
    groups: set[str] = set()
    if not row["active"]:
        pass  # deactivated: no groups at all, RLS shows nothing
    elif row["role"] == "service":
        # service accounts: their job group (the user id itself, "svc:<job>"), no `all`, no user:
        groups = {row["user_id"] if row["user_id"].startswith("svc:") else f"svc:{row['user_id']}"}
        groups |= set(row["memberships"]) | set(row["grants"])
    else:
        groups = {"all", f"dept:{row['dept']}", f"user:{row['user_id']}"}
        if row["role"] == "manager":
            groups.add(f"head:{row['dept']}")
        if row["role"] == "ceo":
            groups.add("head:exec")  # the CEO heads the exec department
        if row["role"] in ("ceo", "platform_admin", "security_auditor"):
            groups.add(f"role:{row['role']}")
        groups |= set(row["memberships"]) | set(row["grants"])
        groups |= {f"mgr-of:{r}" for r in row["direct_reports"]}
    return Principal(user_id=row["user_id"], name=row["name"], email=row["email"], dept=Dept(row["dept"]),
                     role=Role(row["role"]), title=row["title"], manager_id=row["manager_id"],
                     clearance=Level(row["clearance"]), groups=frozenset(groups | extra_groups),
                     active=row["active"])


def test_hand_written_groups_equal_identity_for_every_user(synth_small):
    with pgmod.connect_owner() as c:
        ids = [r["user_id"] for r in c.execute("SELECT user_id FROM core.users ORDER BY user_id").fetchall()]
    assert ids
    for uid in ids:
        mine, theirs = build_principal(uid), get_principal_sync(uid)
        assert mine is not None and theirs is not None, uid
        assert mine == theirs, (uid, mine.groups ^ theirs.groups)


def test_principal_for_is_identitys_principal(principal_for):
    from corpai.synth.personas import PERSONAS

    for uid in PERSONAS.values():
        assert principal_for(uid) == get_principal_sync(uid)
