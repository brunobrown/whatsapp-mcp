"""Routes of the sections: the menu, one section's schema and values, a check and a save.

A save answers 200 when it worked or changed nothing, 409 when the file changed on disk after the client loaded it or
cannot be read and needs a confirmation to be replaced, and 422 when a value was refused. The body is always the
SaveOutcome. Handlers are plain functions and the work that touches files or the bridge runs off the event loop.
"""
from typing import Any, Dict, List, Optional, Tuple

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ui.api import security
from ui.api.forms import FormService
from ui.api.routes_session import bad_request

NOT_FOUND = "There is no such section."


def not_found() -> JSONResponse:
    return security.refusal(404, "not_found", NOT_FOUND)


def _read_edit(body: Any) -> Optional[Tuple[str, Dict[str, Any], List[Any], bool]]:
    """(version, changes, restore, confirm_replace) of a well-formed request, or None."""
    if not isinstance(body, dict):
        return None
    version, changes = body.get("version"), body.get("changes", {})
    restore, confirm = body.get("restore", []), body.get("confirm_replace", False)
    if not (isinstance(version, str) and isinstance(changes, dict) and isinstance(restore, list)
            and isinstance(confirm, bool)):
        return None
    return version, changes, restore, confirm


def section_routes(forms: FormService) -> List[Route]:
    async def listing(request: Request) -> Response:
        return JSONResponse(forms.listing())

    def detail(request: Request) -> Response:
        try:
            flow = forms.open(request.path_params["section_id"])
        except KeyError:
            return not_found()
        return JSONResponse(forms.view(flow))

    def check(section_id: str, edit: Tuple[str, Dict[str, Any], List[Any], bool]) -> Response:
        version, changes, restore, _ = edit
        try:
            flow = forms.open(section_id)
        except KeyError:
            return not_found()
        forms.apply(flow, changes, restore, version)
        return JSONResponse(forms.validate(flow))

    def store(section_id: str, edit: Tuple[str, Dict[str, Any], List[Any], bool]) -> Response:
        version, changes, restore, confirm = edit
        try:
            flow = forms.open(section_id)
        except KeyError:
            return not_found()
        forms.apply(flow, changes, restore, version)
        outcome = forms.save(flow, confirm)
        if outcome["conflict"] or outcome["needs_confirmation"]:
            status = 409
        elif not outcome["ok"]:
            status = 422
        else:
            status = 200
        return JSONResponse(outcome, status_code=status)

    async def _body(request: Request) -> Any:
        try:
            return await request.json()
        except ValueError:
            return None

    async def validate(request: Request) -> Response:
        edit = _read_edit(await _body(request))
        if edit is None:
            return bad_request()
        return await run_in_threadpool(check, request.path_params["section_id"], edit)

    async def save(request: Request) -> Response:
        edit = _read_edit(await _body(request))
        if edit is None:
            return bad_request()
        return await run_in_threadpool(store, request.path_params["section_id"], edit)

    return [Route("/api/sections", listing, methods=["GET"]),
            Route("/api/sections/{section_id}", detail, methods=["GET"]),
            Route("/api/sections/{section_id}/validate", validate, methods=["POST"]),
            Route("/api/sections/{section_id}", save, methods=["PUT"])]
