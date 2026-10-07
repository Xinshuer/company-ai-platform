"""The egress inventory (config/egress.yaml) and the ops.egress_log table's own protections."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

import psycopg
import pytest

from corpai.config import ROOT
from corpai.db import pg as pgmod
from corpai.gateway import EgressDenied, egress, load_config
from corpai.types import Dept, Level, Role, Route

pytestmark = pytest.mark.security


def test_inventory_lists_required_destinations():
    inv = egress.inventory()
    assert inv["model.external"].max_level == Level.L2
    assert inv["model.local"].max_level == Level.L4 and inv["model.local"].simulated_onprem
    assert inv["search.mock"].max_level == Level.L1 and inv["search.mock"].kind == "in_process"
    assert inv["email.outbox"].max_level is None and inv["email.outbox"].kind == "simulated"


def test_every_model_route_is_listed_with_its_host():
    cfg = load_config()
    inv = egress.inventory()
    for r in Route:
        rc = cfg.route(r)
        assert rc.egress in inv, f"route {r} sends to an unlisted destination"
        assert urlsplit(rc.base_url).hostname in inv[rc.egress].hosts
    # the external route can never be configured to accept L3
    assert inv[cfg.route(Route.EXTERNAL).egress].max_level <= Level.L2


@pytest.mark.parametrize("dest,url,level", [
    ("model.unknown", "https://api.deepseek.com", Level.L1),          # unlisted destination
    ("model.external", "https://evil.example", Level.L1),             # unlisted host
    ("model.external", "https://api.deepseek.com.evil.example", Level.L1),
    ("model.external", "http://api.deepseek.com", Level.L1),          # not https
    ("model.external", "https://api.deepseek.com", Level.L3),         # level above max
    ("model.external", "https://api.deepseek.com", 9),                # invalid level
    ("search.mock", None, Level.L2),
    ("email.outbox", None, Level.L1),                                 # model payloads never go there
])
def test_check_egress_denies(dest, url, level):
    with pytest.raises(EgressDenied):
        egress.check_egress(dest, url, level)


def test_check_egress_allows_listed():
    assert egress.check_egress("model.external", "https://api.deepseek.com/v1", Level.L2).name == "model.external"
    assert egress.check_egress("model.local", "https://API.deepseek.com", Level.L4).name == "model.local"
    assert egress.check_egress("search.mock", None, Level.L1).name == "search.mock"


def test_malformed_inventory_rejected():
    for raw in [{}, {"destinations": {"x": {"kind": "ftp"}}},
                {"destinations": {"x": {"kind": "http", "hosts": [], "max_level": "L1"}}},
                {"destinations": {"x": {"kind": "http", "hosts": ["h"], "max_level": "L7"}}}]:
        with pytest.raises(ValueError):
            egress.parse_inventory(raw)


_URL_RE = re.compile(r"https?://([A-Za-z0-9.\-]+)")
_LOCAL_HOSTS = {"127.0.0.1", "localhost"}


def test_no_unlisted_hosts_in_code_or_config():
    """Static half of 'code cannot contact an unlisted host': every http(s) host literal in corpai/
    and config/ is in egress.yaml, local, or a synthetic *.example domain (simulated, in-process).
    The runtime half is the per-request host check in OpenAICompatBackend."""
    listed = set().union(*(d.hosts for d in egress.inventory().values()))
    bad = []
    for base in ("corpai", "config"):
        for path in (ROOT / base).rglob("*"):
            if path.suffix not in (".py", ".yaml", ".yml") or "__pycache__" in path.parts:
                continue
            for host in _URL_RE.findall(path.read_text(encoding="utf-8")):
                host = host.lower().rstrip(".")
                if not host or "." not in host and host not in _LOCAL_HOSTS:
                    continue  # f-string templates like https://www.{domain}
                if host in listed or host in _LOCAL_HOSTS or host.endswith(".example") or host == "www":
                    continue
                bad.append(f"{path.relative_to(ROOT)}: {host}")
    assert bad == [], "hosts missing from config/egress.yaml:\n" + "\n".join(bad)


def test_only_the_gateway_imports_model_sdks():
    """Agents hold no keys and no clients: the openai SDK is imported only inside corpai/gateway."""
    offenders = []
    for path in (ROOT / "corpai").rglob("*.py"):
        if "gateway" in path.relative_to(ROOT / "corpai").parts[:1]:
            continue
        text = path.read_text(encoding="utf-8")
        if re.search(r"^\s*(import openai|from openai\b)", text, re.MULTILINE):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


# ---- ops.egress_log protections -----------------------------------------------------------------

@pytest.mark.parametrize("who", ["staff", "admin", "auditor", "ceo"])
def test_egress_log_readable_only_by_owner(pg, mkp, who):
    p = {"staff": mkp("u0031"), "admin": mkp("u0067", Role.PLATFORM_ADMIN, Dept.IT, Level.L2),
         "auditor": mkp("u0068", Role.SECURITY_AUDITOR, Dept.IT, Level.L2),
         "ceo": mkp("u0001", Role.CEO, Dept.EXEC, Level.L4)}[who]
    for sql in ("SELECT * FROM ops.egress_log", "DELETE FROM ops.egress_log",
                "UPDATE ops.egress_log SET payload = ''",
                "INSERT INTO ops.egress_log (destination, route, level, user_id, payload, payload_sha256) "
                "VALUES ('x', 'external', 1, 'u', 'p', repeat('0', 64))"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with pgmod.principal_tx_sync(p) as conn:
                conn.execute(sql)


def test_log_egress_refuses_l3_external_and_anonymous(pg, staff):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with pgmod.principal_tx_sync(staff) as conn:
            conn.execute("SELECT ops.log_egress('model.external', 'external', 3::smallint, 'r', 'x')")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with pgmod.connect_app() as conn:
            conn.execute("SELECT ops.log_egress('model.external', 'external', 1::smallint, 'r', 'x')")
    # even the owner cannot store an L3 external row (CHECK constraint)
    with pytest.raises(psycopg.errors.CheckViolation):
        with pgmod.connect_owner() as conn:
            conn.execute("INSERT INTO ops.egress_log (destination, route, level, user_id, payload, payload_sha256) "
                         "VALUES ('x', 'external', 3, 'u', 'p', repeat('0', 64))")


def test_log_egress_takes_user_from_session_not_parameter(pg, mkp):
    p = mkp("u0042")
    with pgmod.principal_tx_sync(p) as conn:
        eid = conn.execute("SELECT ops.log_egress('model.external', 'external', 1::smallint, 'run_t', 'hello') AS id"
                           ).fetchone()["id"]
    with pgmod.connect_owner() as c:
        row = c.execute("SELECT * FROM ops.egress_log WHERE id = %s", (eid,)).fetchone()
    import hashlib
    assert row["user_id"] == "u0042"
    assert row["payload_sha256"] == hashlib.sha256(b"hello").hexdigest()
