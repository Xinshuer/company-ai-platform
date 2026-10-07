"""Helpers for the API tests: demo login, owner-side reads, switch flips."""

from __future__ import annotations

from typing import Any

from corpai.api.security import COOKIE


async def login(client: Any, user_id: str) -> None:
    client.cookies.clear()
    r = await client.post("/api/auth/demo-login", json={"user_id": user_id})
    assert r.status_code == 200, r.text
    assert COOKIE in client.cookies


async def run(client: Any, text: str, *, app: str = "qa", thread_id: str | None = None, user_level: int = 1) -> dict:
    r = await client.post("/api/runs", json={"app": app, "input": text, "thread_id": thread_id,
                                             "user_level": user_level})
    assert r.status_code == 200, r.text
    return r.json()


def owner_rows(sql: str, params: tuple = ()) -> list[dict]:
    from corpai.db import pg

    with pg.connect_owner() as conn:
        return conn.execute(sql, params).fetchall()


def owner_exec(sql: str, params: tuple = ()) -> None:
    from corpai.db import pg

    with pg.connect_owner() as conn:
        conn.execute(sql, params)


def set_switch_owner(name: str, on: bool) -> None:
    """Flip an outage switch directly (test setup; the API path is tested separately)."""
    owner_exec("UPDATE ops.switches SET \"on\" = %s WHERE name = %s", (on, name))
