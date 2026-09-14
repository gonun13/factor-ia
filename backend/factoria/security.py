"""Origin check on mutating requests.

`DESIGN.md`, Non-functional requirements, taken from Overseer §6: this is
remote code execution as a service. The port is on loopback, which keeps the
LAN out — it does **not** keep out the dev's own browser. Any page they have
open can `fetch('http://127.0.0.1:8000/api/...', {method:'POST'})`, and
without this check that page can create projects, rewrite factories and, once
stage 5 lands, start runs. CORS response headers do not help: the browser
sends the request and only withholds the *response*, by which time the write
has happened.

The rule, applied to POST/PUT/PATCH/DELETE only:

- **No `Origin` header → allow.** `curl`, a health probe and a script have no
  origin to speak of, and no browser omits `Origin` on a cross-origin mutating
  request. Refusing here would break every CLI caller to stop nobody.
- **`Origin` matching the request's own `Host` → allow.** This is the SPA that
  FastAPI itself served, whatever host:port the operator reached it on
  (`localhost:8000` and `127.0.0.1:8000` are each self-consistent).
- **`Origin` in `FACTOR_IA_ALLOWED_ORIGINS` → allow.** The escape hatch for the
  Vite dev server, which proxies `/api` while the page's origin stays
  `http://localhost:5173`.
- **Anything else, `null` included → 403.** `null` is what a sandboxed iframe
  or a `file://` page sends; it is not a trusted origin, it is the absence of
  one where a browser has explicitly stated there was a page.

Reads are not checked. A cross-origin GET is a request the browser will not
show the attacker anyway, and SSE (stage 5) is a GET.
"""

from __future__ import annotations

import os

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def configured_allowed_origins() -> frozenset[str]:
    raw = os.environ.get("FACTOR_IA_ALLOWED_ORIGINS", "")
    return frozenset(item.strip().rstrip("/") for item in raw.split(",") if item.strip())


def origin_is_allowed(origin: str | None, host: str | None, allowed: frozenset[str]) -> bool:
    """Whether a mutating request bearing `origin` may proceed."""
    if origin is None:
        return True  # not a browser; see module docstring
    origin = origin.strip().rstrip("/")
    if not origin or origin == "null":
        return False
    if origin in allowed:
        return True
    if not host:
        return False
    # Scheme is not in the Host header, so both are accepted for the same
    # authority. Loopback HTTP is the normal case; a reverse proxy in front
    # would terminate TLS and still forward this Host.
    return origin in (f"http://{host}", f"https://{host}")


class OriginCheckMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if request.method.upper() in MUTATING_METHODS:
            origin = request.headers.get("origin")
            host = request.headers.get("host")
            if not origin_is_allowed(origin, host, configured_allowed_origins()):
                return JSONResponse(
                    status_code=403,
                    content={
                        "detail": (
                            "cross-origin request refused: this server only accepts "
                            "mutating requests from its own origin"
                        )
                    },
                )
        return await call_next(request)
