"""Tool and gateway tokens (no database): claims, expiry, audience, tampering, scope, secrecy."""

from __future__ import annotations

import base64
import json
import time
from dataclasses import replace

import jwt
import pytest

from corpai.identity import keys
from corpai.identity.tokens import (
    BadSignature,
    NotInScope,
    TokenError,
    TokenExpired,
    WrongAudience,
    mint_gateway_token,
    mint_tool_token,
    require_scope,
    verify_gateway_token,
    verify_tool_token,
)
from corpai.types import Level
from tests.policy.fake_personas import persona

P = persona("sales_staff")


def _mint(**kw) -> str:
    args = {"domain": "crm", "agent": "sales_profile", "run_id": "run_1", "tools": ["get_account"]}
    args.update(kw)
    return mint_tool_token(P, **args)


def _forge(claims: dict, key_name: str = keys.TOOL, alg: str = "HS256") -> str:
    return jwt.encode(claims, keys.load_key(key_name), algorithm=alg)


def _claims(**over) -> dict:
    now = int(time.time())
    c = {"iss": "corpai", "aud": "mcp:crm", "sub": P.user_id, "act": {"sub": "agent:sales_profile"},
         "scope": "get_account", "run_id": "run_1", "jti": "jti_x", "iat": now, "exp": now + 60}
    c.update(over)
    return {k: v for k, v in c.items() if v is not None}


def _assert_secret(exc: BaseException, token: str) -> None:
    """No token text anywhere in the exception, and no chained PyJWT exception carrying it."""
    sig = token.rsplit(".", 1)[-1]
    for text in (str(exc), repr(exc), str(exc.args)):
        assert token not in text and (not sig or sig not in text)
        assert token.split(".")[0] not in text  # not even the header
    assert exc.__cause__ is None and (exc.__context__ is None or exc.__suppress_context__)


# ---- tool tokens -------------------------------------------------------------------------------

def test_tool_token_roundtrip_and_claims():
    t = _mint(tools=["get_account", "search_accounts"], ttl_s=60)
    c = verify_tool_token(t, domain="crm")
    assert c.sub == P.user_id and c.agent == "sales_profile" and c.domain == "crm"
    assert c.aud == "mcp:crm" and c.act == {"sub": "agent:sales_profile"} and c.iss == "corpai"
    assert c.scope == {"get_account", "search_accounts"} and c.run_id == "run_1"
    assert c.jti.startswith("jti_") and c.exp - c.iat == 60
    raw = jwt.decode(t, options={"verify_signature": False})
    assert raw["aud"] == "mcp:crm" and raw["act"] == {"sub": "agent:sales_profile"}
    assert raw["scope"] == "get_account search_accounts"
    assert set(raw) == {"iss", "aud", "sub", "act", "scope", "run_id", "jti", "iat", "exp"}


def test_each_token_has_a_fresh_jti():
    a, b = _mint(), _mint()
    assert verify_tool_token(a, domain="crm").jti != verify_tool_token(b, domain="crm").jti


def test_scope():
    c = verify_tool_token(_mint(tools=["get_account"]), domain="crm")
    require_scope(c, "get_account")
    with pytest.raises(NotInScope):
        require_scope(c, "send_email")
    with pytest.raises(NotInScope):
        require_scope(c, "get_account send_email")


def test_wrong_audience():
    t = _mint()
    with pytest.raises(WrongAudience) as e:
        verify_tool_token(t, domain="finance")
    _assert_secret(e.value, t)


def test_expired_and_leeway():
    now = int(time.time())
    t = _forge(_claims(iat=now - 100, exp=now - 30))
    with pytest.raises(TokenExpired) as e:
        verify_tool_token(t, domain="crm")
    _assert_secret(e.value, t)
    # within the 5 s leeway still valid
    verify_tool_token(_forge(_claims(iat=now - 60, exp=now - 2)), domain="crm")


def test_iat_in_future_rejected():
    now = int(time.time())
    with pytest.raises(TokenError):
        verify_tool_token(_forge(_claims(iat=now + 120, exp=now + 180)), domain="crm")


def test_tampered_signature():
    t = _mint()
    head, payload, sig = t.split(".")
    flipped = sig[:-2] + ("A" if sig[-2] != "A" else "B") + sig[-1]
    bad = f"{head}.{payload}.{flipped}"
    with pytest.raises(BadSignature) as e:
        verify_tool_token(bad, domain="crm")
    _assert_secret(e.value, bad)


def test_tampered_payload_scope_escalation():
    t = _mint(tools=["get_account"])
    head, payload, sig = t.split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    claims["scope"] = "get_account send_email"
    forged = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    bad = f"{head}.{forged}.{sig}"
    with pytest.raises(BadSignature) as e:
        verify_tool_token(bad, domain="crm")
    _assert_secret(e.value, bad)


def test_signed_with_another_key():
    for key_name in (keys.SESSION, keys.GATEWAY):
        t = _forge(_claims(), key_name=key_name)
        with pytest.raises(BadSignature):
            verify_tool_token(t, domain="crm")


def test_alg_none_rejected():
    t = jwt.encode(_claims(), key=None, algorithm="none")
    with pytest.raises(TokenError) as e:
        verify_tool_token(t, domain="crm")
    _assert_secret(e.value, t)


@pytest.mark.parametrize("over", [
    {"act": None}, {"act": {"sub": "user:u0004"}}, {"act": "agent:qa"}, {"act": {"sub": "agent:Bad Name"}},
    {"iss": "evil"}, {"scope": None}, {"scope": ""}, {"scope": "get_account  send_email"},
    {"run_id": None}, {"jti": None}, {"sub": None},
])
def test_malformed_claims_rejected(over):
    t = _forge(_claims(**over))
    with pytest.raises(TokenError) as e:
        verify_tool_token(t, domain="crm")
    _assert_secret(e.value, t)


def test_overlong_lifetime_rejected_even_if_signed():
    now = int(time.time())
    with pytest.raises(TokenError):
        verify_tool_token(_forge(_claims(iat=now, exp=now + 86400)), domain="crm")


@pytest.mark.parametrize("junk", ["", "abc", "a.b.c", "x" * 9000, None, 42])
def test_garbage_tokens(junk):
    with pytest.raises(TokenError):
        verify_tool_token(junk, domain="crm")


def test_mint_refuses_inactive_and_bad_inputs():
    with pytest.raises(TokenError):
        mint_tool_token(replace(P, active=False), domain="crm", agent="qa", run_id="r", tools=["get_account"])
    for bad in ({"tools": []}, {"tools": ["get_account send_email"]}, {"tools": ["Get"]},
                {"domain": "mcp:crm"}, {"agent": "agent:qa"}, {"run_id": ""}, {"ttl_s": 0},
                {"ttl_s": 100_000}, {"ttl_s": True}):
        with pytest.raises(TokenError):
            _mint(**bad)


# ---- gateway tokens ----------------------------------------------------------------------------

def test_gateway_roundtrip():
    t = mint_gateway_token(P, agent="legal_review", run_id="run_9", floor=Level.L3)
    c = verify_gateway_token(t)
    assert (c.sub, c.agent, c.run_id, c.floor) == (P.user_id, "legal_review", "run_9", Level.L3)
    assert c.exp - c.iat == 900
    assert jwt.decode(t, options={"verify_signature": False})["aud"] == "gateway"


def test_gateway_and_tool_tokens_not_interchangeable():
    g = mint_gateway_token(P, agent="qa", run_id="r", floor=Level.L1)
    with pytest.raises(TokenError):
        verify_tool_token(g, domain="crm")
    with pytest.raises(TokenError):
        verify_gateway_token(_mint())


def test_gateway_wrong_audience_and_bad_floor():
    now = int(time.time())
    base = {"iss": "corpai", "aud": "gateway", "sub": P.user_id, "act": {"sub": "agent:qa"},
            "run_id": "r", "floor": 2, "jti": "j", "iat": now, "exp": now + 60}
    with pytest.raises(WrongAudience):
        verify_gateway_token(_forge({**base, "aud": "mcp:crm"}, keys.GATEWAY))
    for floor in (0, 5, "3", None):
        with pytest.raises(TokenError):
            verify_gateway_token(_forge({**base, "floor": floor}, keys.GATEWAY))
    with pytest.raises(TokenExpired):
        verify_gateway_token(_forge({**base, "iat": now - 100, "exp": now - 50}, keys.GATEWAY))


def test_gateway_refuses_inactive():
    with pytest.raises(TokenError):
        mint_gateway_token(replace(P, active=False), agent="qa", run_id="r", floor=Level.L1)


# ---- keys --------------------------------------------------------------------------------------

def test_key_file_created_hex_32_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(keys, "_path", lambda name: tmp_path / name)
    k = keys.load_key(keys.TOOL)
    assert len(k) == 32
    assert (tmp_path / keys.TOOL).read_text() == k.hex()
    assert keys.load_key(keys.TOOL) == k  # stable
    assert keys.load_key(keys.SESSION) != k  # separate keys


def test_deleting_key_invalidates_tokens(tmp_path, monkeypatch):
    monkeypatch.setattr(keys, "_path", lambda name: tmp_path / name)
    t = _mint()
    verify_tool_token(t, domain="crm")
    (tmp_path / keys.TOOL).unlink()
    with pytest.raises(BadSignature):
        verify_tool_token(t, domain="crm")


@pytest.mark.parametrize("content", ["not-hex", "abcd"])
def test_malformed_key_fails_closed(tmp_path, monkeypatch, content):
    monkeypatch.setattr(keys, "_path", lambda name: tmp_path / name)
    (tmp_path / keys.TOOL).write_text(content)
    with pytest.raises(keys.KeyFileError) as e:
        _mint()
    assert content not in str(e.value)


def test_claims_repr_has_no_token():
    t = _mint()
    c = verify_tool_token(t, domain="crm")
    assert t not in repr(c) and t.rsplit(".", 1)[-1] not in repr(c)
