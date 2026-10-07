"""Report export (M3; design-review 二.7): the watermarked file of a finished management report.

    POST /api/reports/{run_id}/export   {"thread_id": "thr_..."}
        -> {"filename", "content", "sha256", "level", "domain", "bytes"}   (the UI saves it as a file)
    GET  /api/reports/{run_id}/export?thread_id=thr_...
        -> the same file as a text/markdown download

The report text comes from the caller's own thread checkpoint, never from the request; the domain
and the watermark level come from code (corpai.agents.report.export). Every export is audited by
corpai.metrics.aexport_report (event 'export', allow or deny, metadata only); a request refused
before that policy check (no such run, someone else's, a role that may not run reports) gets an
'export' deny row from export_run. The report runs
themselves go through POST /api/runs with app "report".
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel, Field

from corpai.agents import router as app_router
from corpai.agents.report import ExportFile, ExportNotFound, export_run
from corpai.api import deps
from corpai.api.errors import ApiError, forbidden, not_found
from corpai.types import Principal

router = APIRouter()

_NOT_FOUND = "No such report among yours; run the report first."


class ExportIn(BaseModel):
    thread_id: str = Field(min_length=1, max_length=64)


async def _export(p: Principal, run_id: str, thread_id: str, checkpointer: Any) -> ExportFile:
    try:
        return await export_run(p, run_id=run_id, thread_id=thread_id, checkpointer=checkpointer)
    except ExportNotFound:
        raise not_found(_NOT_FOUND) from None
    except app_router.RouterError as e:
        raise ApiError(e.status, e.code, "Your role may not run or export management reports.") from None
    except PermissionError:
        raise forbidden("Your role may not export this report.") from None


@router.post("/api/reports/{run_id}/export")
async def export_json(run_id: str, body: ExportIn, p: Principal = Depends(deps.principal),
                      checkpointer: Any = Depends(deps.checkpointer)) -> dict:
    f = await _export(p, run_id, body.thread_id, checkpointer)
    return {"filename": f.filename, "content": f.content.decode("utf-8"), "sha256": f.sha256, "level": f.level,
            "domain": f.domain, "bytes": len(f.content)}


@router.get("/api/reports/{run_id}/export")
async def export_file(run_id: str, thread_id: str = "", p: Principal = Depends(deps.principal),
                      checkpointer: Any = Depends(deps.checkpointer)) -> Response:
    f = await _export(p, run_id, thread_id, checkpointer)
    # the filename is built by code from a slug, the run id and a timestamp: [a-z0-9-_.] only
    return Response(content=f.content, media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{f.filename}"',
                             "X-Export-Sha256": f.sha256})
