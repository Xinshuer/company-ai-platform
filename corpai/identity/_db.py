"""Principal-less app-role queries for identity (directory lookups, sessions).

Identity runs before a principal exists, so it cannot use principal_tx. It only ever calls
SECURITY DEFINER functions in `core`; the app role has no table rights there.
"""

from __future__ import annotations

from typing import Any, Sequence


from corpai.db import pg


def fetch_sync(sql: str, params: Sequence[Any] = ()) -> list[dict]:
    with pg.connect_app() as conn:  # commits on clean exit (session functions write)
        return conn.execute(sql, params).fetchall()


async def fetch_async(sql: str, params: Sequence[Any] = ()) -> list[dict]:
    async with pg.app_tx() as conn:  # reuses the API's pool when one is installed
        cur = await conn.execute(sql, params)
        return await cur.fetchall()
