"""scrub_secrets: rule 5, secrets never reach a model (both routes)."""

from __future__ import annotations

import pytest

from corpai.classify import classify_text, scrub_secrets
from corpai.types import Level

SECRETS = [
    ("openai_key", "sk-abcdefghijklmnopqrstuvwxyz123456"),
    ("openai_key", "sk-proj-AbCdEfGhIjKlMnOpQrStUvWx_12"),
    ("aws_key", "AKIAIOSFODNN7EXAMPLE"),
    ("github_token", "ghp_abcdefghijklmnopqrstuvwxyz0123456789"),
    ("github_token", "gho_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"),
    ("slack_token", "xoxb-1234567890-abcdefghij"),
    ("jwt", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1MDAwMSJ9.c2lnbmF0dXJlLXZhbHVl"),
    ("private_key", "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASC\n-----END PRIVATE KEY-----"),
    ("private_key", "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAA\n-----END OPENSSH PRIVATE KEY-----"),
]


@pytest.mark.parametrize("kind,secret", SECRETS, ids=[f"{k}-{i}" for i, (k, _) in enumerate(SECRETS)])
def test_each_secret_kind_is_scrubbed(kind, secret):
    text = f"before {secret} after"
    out, n = scrub_secrets(text)
    assert n == 1
    assert secret not in out
    assert out == f"before [REDACTED:{kind}] after"
    assert classify_text(text).level == Level.L4


@pytest.mark.parametrize("text,value", [
    ("password=hunter2", "hunter2"),
    ("db password: hunter2!", "hunter2!"),
    ("PASSWD = s3cr3t", "s3cr3t"),
    ("pwd:abc123", "abc123"),
    ("api_key=AbC123xyz", "AbC123xyz"),
    ("API-KEY: AbC123xyz", "AbC123xyz"),
    ("DEEPSEEK_API_KEY=not-a-real-key-123", "not-a-real-key-123"),
    ("client_secret: 'q1w2e3r4'", "'q1w2e3r4'"),
    ('{"token": "abc.def.ghi"}', '"abc.def.ghi"'),
    ("Passwort=Geheim123", "Geheim123"),
    ("服务器密码：Abc12345", "Abc12345"),
])
def test_credential_assignments_keep_the_key_and_drop_the_value(text, value):
    out, n = scrub_secrets(text)
    assert n == 1
    assert value not in out
    assert "[REDACTED:password]" in out
    assert out.split("[REDACTED")[0].strip(), "the key name stays visible for debugging"


def test_unterminated_private_key_is_scrubbed_to_the_end():
    text = "here:\n-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEAtruncated...\nmore lines"
    out, n = scrub_secrets(text)
    assert n == 1 and "MIIE" not in out and "more lines" not in out


def test_multiple_secrets_and_counts():
    text = "a sk-abcdefghijklmnopqrstuvwxyz123456 b AKIAIOSFODNN7EXAMPLE c password=x1y2z3"
    out, n = scrub_secrets(text)
    assert n == 3
    assert out == "a [REDACTED:openai_key] b [REDACTED:aws_key] c password=[REDACTED:password]"


def test_idempotent():
    once, n1 = scrub_secrets("password=hunter2 and sk-abcdefghijklmnopqrstuvwxyz123456")
    twice, n2 = scrub_secrets(once)
    assert n1 == 2 and n2 == 0 and once == twice


@pytest.mark.parametrize("text", [
    "How do I rotate an API key?",
    "The token budget is shared per department.",
    "max_tokens=1200 and temperature=0.2",
    "scikit-learn uses sk-style names like sk-learn",
    "Password policy: at least 12 characters.",  # "policy:" is not "password:"
    "KB-CANARY-0A1B2C3D",  # canaries are detected, never scrubbed: the egress scan must still see them
])
def test_non_secrets_untouched(text):
    out, n = scrub_secrets(text)
    assert out == text and n == 0


@pytest.mark.parametrize("prefix", ["password=", "sk-", "ghp_", "Bearer "])
def test_long_secrets_are_removed_whole(prefix):
    text = f"x {prefix}{'a' * 200_000} y"
    out, n = scrub_secrets(text)
    assert n == 1
    assert "aaaa" not in out
    assert out.endswith(" y")


# ---- hidden characters: a model reads straight through them, so the scrubber must too ----------

def _fullwidth(s: str) -> str:
    return "".join(chr(ord(c) + 0xFEE0) if "!" <= c <= "~" else c for c in s)


def _with_hidden(secret: str, ch: str) -> str:
    # inside the distinctive part of the secret, away from a private key's header line
    i = min(len(secret) // 2, 12)
    return secret[:i] + ch + secret[i:]


_HIDDEN_VARIANTS = [(f"{k}-zwsp-{i}", k, _with_hidden(s, "​")) for i, (k, s) in enumerate(SECRETS)] + \
    [(f"{k}-shy-{i}", k, _with_hidden(s, "­")) for i, (k, s) in enumerate(SECRETS)] + \
    [(f"{k}-wordjoiner-{i}", k, _with_hidden(s, "⁠")) for i, (k, s) in enumerate(SECRETS)] + \
    [(f"{k}-fullwidth-{i}", k, _fullwidth(s)) for i, (k, s) in enumerate(SECRETS)]


@pytest.mark.parametrize("kind,secret", [(k, s) for _, k, s in _HIDDEN_VARIANTS],
                         ids=[i for i, _, _ in _HIDDEN_VARIANTS])
def test_secrets_hidden_by_invisible_or_fullwidth_characters_are_scrubbed(kind, secret):
    text = f"before {secret} after"
    out, n = scrub_secrets(text)
    assert n == 1
    assert out == f"before [REDACTED:{kind}] after"
    # classify_text and scrub_secrets agree: whatever classify calls a secret is gone after scrubbing
    assert classify_text(text).level == Level.L4
    assert "secret" not in classify_text(out).kinds


def test_hidden_credential_assignment_value_is_scrubbed():
    out, n = scrub_secrets(_fullwidth("password") + "=hunter2 rest")
    assert n == 1 and "hunter2" not in out and out.endswith(" rest")
