"""The built SPA (ui/web) and what is served when it is not there.

The handler answers every path no route claimed: a file of ui/web (hashed files under assets/ are cached for a year,
everything else must be revalidated), index.html for a path of the SPA, and a JSON 404 under /api. A file outside ui/web
is never read, whatever the path says. Without ui/web/index.html the root is a short page without script that says
how to get the SPA, and the API keeps answering.
"""
import sys
from pathlib import Path
from typing import Any, Callable, Optional

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response

ASSET_CACHE = "public, max-age=31536000, immutable"
REVALIDATE = "no-cache"
MISSING_NOTICE = ("ui: the web pages are not installed (ui/web/index.html is missing), so the interface shows instructions "
                  "instead. Download whatsapp-mcp-ui-dist.tar.gz from the release, or build it with "
                  "'npm ci && npm run build' in whatsapp-mcp-server/frontend.")
MISSING_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>WhatsApp MCP - Settings</title>
<style>body{font-family:system-ui,sans-serif;max-width:40rem;margin:3rem auto;padding:0 1rem;line-height:1.5}
code{background:#eee;padding:.1rem .3rem;border-radius:.2rem}</style>
</head>
<body>
<h1>The interface pages are not installed</h1>
<p>The server is running and its API answers, but the pages of the interface (<code>ui/web</code>) are missing.</p>
<p>Get them in one of two ways:</p>
<ol>
<li>Download <code>whatsapp-mcp-ui-dist.tar.gz</code> from the release of this version and extract it into
<code>whatsapp-mcp-server/ui/web</code>.</li>
<li>Build them from the source: run <code>npm ci &amp;&amp; npm run build</code> in
<code>whatsapp-mcp-server/frontend</code> (needs Node 24).</li>
</ol>
<p>Then reload this page.</p>
</body>
</html>
"""


def _is_api(path: str) -> bool:
    return path == "/api" or path.startswith("/api/")


def _inside(root: Path, path: str) -> Optional[Path]:
    """The file of `root` the URL path names, or None when it names nothing there or leaves `root`."""
    relative = path.lstrip("/")
    if not relative or "\x00" in relative or "\\" in relative:
        return None
    if any(part == ".." or part.startswith(".") for part in relative.split("/")):
        return None
    try:
        target = (root / relative).resolve()
        target.relative_to(root)
    except (ValueError, OSError):
        return None
    return target if target.is_file() else None


def static_handler(web_dir: Optional[Path], notice: Callable[[str], Any] = lambda line: print(line, file=sys.stderr, flush=True)):
    """The handler for paths no route claimed. `notice` hears once, now, if the SPA is missing."""
    root = Path(web_dir).resolve() if web_dir is not None else None
    if root is None or not (root / "index.html").is_file():
        notice(MISSING_NOTICE)

    async def handle(request: Request, exc: Optional[Exception] = None) -> Response:
        if isinstance(exc, HTTPException) and exc.status_code != 404:
            return JSONResponse({"code": "error", "message": exc.detail}, status_code=exc.status_code)
        path = request.scope["path"]
        if _is_api(path) or request.method not in ("GET", "HEAD"):
            return JSONResponse({"code": "not_found", "message": "There is no such endpoint."}, status_code=404)
        index = root / "index.html" if root is not None else None
        if index is None or not index.is_file():
            return HTMLResponse(MISSING_PAGE, status_code=200 if path == "/" else 404, headers={"Cache-Control": "no-store"})
        target = _inside(root, path)
        if target is None:
            return FileResponse(index, headers={"Cache-Control": REVALIDATE})
        first = target.relative_to(root).parts[0]
        return FileResponse(target, headers={"Cache-Control": ASSET_CACHE if first == "assets" else REVALIDATE})

    return handle
