"""Read-only routes of the status and activity pages. What they return is decided in ui.sections.ops, which copies
only listed fields; a bridge that does not answer gives `{"unavailable": reason, "message": ...}` with status 200."""
from typing import List

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ui.api import security
from ui.sections import ops

AUDIT_KINDS = ("send", "monitor")


def ops_routes() -> List[Route]:
    def status(request: Request) -> Response:
        return JSONResponse(ops.status())

    def counters(request: Request) -> Response:
        return JSONResponse(ops.counters())

    def drafts(request: Request) -> Response:
        return JSONResponse(ops.drafts_view())

    def audit(request: Request) -> Response:
        kind = request.path_params["kind"]
        if kind not in AUDIT_KINDS:
            return security.refusal(404, "not_found", "There is no such log.")
        return JSONResponse(ops.audit(kind))

    return [Route("/api/ops/status", status, methods=["GET"]),
            Route("/api/ops/counters", counters, methods=["GET"]),
            Route("/api/ops/drafts", drafts, methods=["GET"]),
            Route("/api/ops/audit/{kind}", audit, methods=["GET"])]
