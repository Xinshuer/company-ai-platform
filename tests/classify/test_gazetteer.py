"""Person-name gazetteer: matching rules and loading from the database."""

from __future__ import annotations

import pytest

from corpai.classify import classify_text, gazetteer_size, load_gazetteer, load_gazetteer_from_db, set_gazetteer
from corpai.classify import names as names_mod
from corpai.types import Level


def _names_found(text: str) -> list[str]:
    return [text[f.start:f.end] for f in classify_text(text).findings if f.kind == "person_name"]


def test_whole_word_and_case_sensitive():
    set_gazetteer(["Anna Becker"])
    assert _names_found("Ask Anna Becker.") == ["Anna Becker"]
    assert _names_found("Ask Anna Beckers team") == []
    assert _names_found("Ask XAnna Becker") == []
    assert _names_found("ask anna becker") == []
    assert _names_found("Anna\nBecker wrote this") == ["Anna\nBecker"]  # wrapped across lines


def test_single_tokens_and_junk_are_ignored():
    n = set_gazetteer(["Madonna", "", "  ", None, "x y", "anna becker", "Jonas Weber", "李"])  # type: ignore[list-item]
    assert n == 1
    assert _names_found("Madonna and Jonas Weber") == ["Jonas Weber"]


def test_longest_name_wins():
    set_gazetteer(["Anna Becker", "Anna Becker-Lange"])
    assert _names_found("Anna Becker-Lange joined") == ["Anna Becker-Lange"]


def test_cjk_names_match_without_spaces():
    set_gazetteer(["王伟", "Anna Becker"])
    assert _names_found("请王伟明天回复") == ["王伟"]
    assert classify_text("请王伟明天回复").level == Level.L3


def test_regex_special_characters_are_escaped():
    set_gazetteer(["Zoë O'Brien", "A.B Cee"])
    assert _names_found("Ping Zoë O'Brien now") == ["Zoë O'Brien"]
    assert _names_found("AxB Cee") == []


def test_large_gazetteer_is_fast():
    import time

    set_gazetteer([f"Firstname{i} Lastname{i}" for i in range(5000)] + ["Anna Becker"])
    text = ("The weekly sync covered routing, invoices and hiring. " * 80)[:4096] + " Anna Becker"
    t0 = time.perf_counter()
    for _ in range(20):
        found = _names_found(text)
    per_call = (time.perf_counter() - t0) / 20
    assert found == ["Anna Becker"]
    assert per_call < 0.05


# ---- database loading ---------------------------------------------------------------------

_TEST_USERS = [
    ("u9901", "Greta Lindqvist", "greta.lindqvist@kestrelbyte.example", "employee"),
    ("u9902", "Tomasz Wiśniewski", "tomasz.w@kestrelbyte.example", "employee"),
    ("svc:gazetteer-test", "Nightly Sync", "svc-gazetteer@kestrelbyte.example", "service"),
]


@pytest.fixture
def directory_rows(pg):
    from corpai.db import pg as pgmod

    with pgmod.connect_owner() as conn:
        for uid, name, email, role in _TEST_USERS:
            conn.execute(
                "INSERT INTO core.users (user_id, name, email, dept, role, title, clearance) "
                "VALUES (%s, %s, %s, 'eng', %s, 'Engineer', 3) ON CONFLICT (user_id) DO NOTHING",
                (uid, name, email, role))
    yield
    with pgmod.connect_owner() as conn:
        conn.execute("DELETE FROM core.users WHERE user_id = ANY(%s)", ([u[0] for u in _TEST_USERS],))


def test_load_from_db_reads_the_directory(directory_rows):
    set_gazetteer([])
    n = load_gazetteer_from_db()
    assert n >= 2
    assert _names_found("Greta Lindqvist and Tomasz Wiśniewski") == ["Greta Lindqvist", "Tomasz Wiśniewski"]
    # service accounts are not people
    assert _names_found("Nightly Sync finished") == []


@pytest.mark.parametrize("bad", [("crm", "no_such_table"), ("no_such_schema", "contacts"),
                                 ('bad"schema', "x;drop")])   # to_regclass rejects the name: a savepoint absorbs it
def test_missing_or_broken_tables_fail_the_load_and_keep_the_old_gazetteer(pg, directory_rows, monkeypatch, bad):
    """A partial gazetteer fails open (names from the missing table classify as L1): refuse it."""
    from corpai.classify import GazetteerError
    from corpai.db import pg as pgmod

    set_gazetteer(["Anna Becker"])
    monkeypatch.setattr(names_mod, "_TABLES", (bad, ("core", "users")))
    with pgmod.connect_owner() as conn:
        with pytest.raises(GazetteerError) as e:
            load_gazetteer(conn)
        # the connection is still usable afterwards (no aborted transaction left behind)
        assert conn.execute("SELECT 1 AS one").fetchone()["one"] == 1
    assert bad[1] in str(e.value)
    assert gazetteer_size() == 1 and _names_found("Anna Becker") == ["Anna Becker"]
    assert _names_found("Greta Lindqvist") == []  # the half-built set was not installed


def test_table_without_a_name_column_fails_the_load(pg, monkeypatch):
    from corpai.classify import GazetteerError
    from corpai.db import pg as pgmod

    monkeypatch.setattr(names_mod, "_TABLES", (("core", "memberships"), ("core", "users")))
    with pgmod.connect_owner() as conn:
        with pytest.raises(GazetteerError):
            load_gazetteer(conn)


def test_default_tables_all_exist_after_migrations(pg):
    """The real source list loads cleanly on a migrated database (crm and hr tables included)."""
    from corpai.db import pg as pgmod

    with pgmod.connect_owner() as conn:
        for schema, table in names_mod._TABLES:
            assert names_mod._names_from_table(conn, schema, table) is not None, (schema, table)
        conn.rollback()


def test_name_column_discovery(pg):
    """crm/hr tables come from another stream; support name, full_name or first_name + last_name."""
    from corpai.db import pg as pgmod

    with pgmod.connect_owner() as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS classify_test")
        conn.execute("CREATE TABLE classify_test.split (first_name text, last_name text)")
        conn.execute("CREATE TABLE classify_test.full (full_name text)")
        conn.execute("CREATE TABLE classify_test.nameless (id int)")
        conn.execute("INSERT INTO classify_test.split VALUES ('Ines', 'Carvalho'), (NULL, NULL)")
        conn.execute("INSERT INTO classify_test.full VALUES ('Mateo Ruiz')")
        try:
            assert names_mod._names_from_table(conn, "classify_test", "split") == ["Ines Carvalho"]
            assert names_mod._names_from_table(conn, "classify_test", "full") == ["Mateo Ruiz"]
            assert names_mod._names_from_table(conn, "classify_test", "nameless") is None
            assert names_mod._names_from_table(conn, "classify_test", "absent") is None
        finally:
            conn.rollback()


def test_gazetteer_size_reflects_state():
    set_gazetteer(["Anna Becker", "Jonas Weber"])
    assert gazetteer_size() == 2
    set_gazetteer([])
    assert gazetteer_size() == 0
    assert _names_found("Anna Becker") == []
