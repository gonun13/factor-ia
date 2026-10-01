"""Who may talk to this server: the `Host` check, and the `Origin` check.

`DESIGN.md`, Non-functional requirements, taken from Overseer §6: this is
remote code execution as a service. The port is on loopback, which keeps the
LAN out — it does **not** keep out the dev's own browser. Any page they have
open can `fetch('http://127.0.0.1:8000/api/...', {method:'POST'})`, and
without these checks that page can create projects, rewrite factories and
start runs. CORS response headers do not help: the browser sends the request
and only withholds the *response*, by which time the write has happened.

**The `Host` check, on every request.** The request's `Host` must be a name
this deployment answers to: `localhost`, `127.0.0.1` or `[::1]` on any port,
plus whatever `FACTOR_IA_ALLOWED_HOSTS` adds. Anything else → 403. This is the
check the `Origin` rule below cannot make on its own: a page is free to have
its own name resolve to `127.0.0.1`, and then its requests arrive here with a
`Host` and an `Origin` that agree with each other and name the attacker. The
browser, not the page, writes `Host`, so a page can never make it say
`localhost` unless it is on `localhost`.

**The `Origin` check, on every `/api` request and every mutating one:**

- **No `Origin` header → allow.** `curl`, a health probe and a script have no
  origin to speak of, and no browser omits `Origin` on a cross-origin mutating
  request. Refusing here would break every CLI caller to stop nobody.
- **`Origin` matching the request's own `Host` → allow.** Safe only *because*
  the `Host` check above has already run: on its own, a self-consistent pair
  says nothing more than what the browser was told to send.
- **`Origin` in `FACTOR_IA_ALLOWED_ORIGINS` → allow.** The escape hatch for the
  Vite dev server, whose proxy rewrites `Host` to this port while the page's
  origin stays `http://localhost:5173`.
- **Anything else, `null` included → 403.** `null` is what a sandboxed iframe
  or a `file://` page sends; it is not a trusted origin, it is the absence of
  one where a browser has explicitly stated there was a page.

**Reads under `/api` are checked too.** A cross-origin `GET` cannot read the
response, but it does not need to: several reads shell out — the agent status
runs the CLI, a codebase's git state runs git — and the side effect happens
whether or not anyone sees the answer. A `no-cors` `GET` carries no `Origin`
at all, so the rule above cannot see it; `Sec-Fetch-Site` can. A request the
browser labels `cross-site` or `same-site` (another port on `localhost` is
same-site) is refused unless its `Origin` is in `FACTOR_IA_ALLOWED_ORIGINS`.
`same-origin` — the SPA, its SSE stream included — and `none` — the operator
typing a URL — pass. Every `/api` read is gated rather than only the ones that
spawn today, so a new route cannot quietly become one.

What stays exempt, and why: the SPA's own files, outside `/api`. Serving a
static file runs nothing, and a link to the app from another page should work.

One limit, stated rather than discovered: a browser too old to send
`Sec-Fetch-Site` (Safari before 16.4) gets the `Origin` rule alone on reads.
"""

from __future__ import annotations

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

from .. import config

MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: The names this server answers to with no configuration: loopback, on any
#: port. A page cannot put these in `Host` unless it is really served from one.
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "[::1]"})

#: `Sec-Fetch-Site` values that mean "another page asked for this".
_FOREIGN_SITES = frozenset({"cross-site", "same-site"})


def configured_allowed_origins() -> frozenset[str]:
    return config.allowed_origins()


def configured_allowed_hosts() -> frozenset[str]:
    return config.allowed_hosts()


def _split_host(host: str) -> tuple[str, str | None]:
    """`Host` as `(name, port)`, with an IPv6 literal kept in its brackets."""
    if host.startswith("["):
        name, _, rest = host.partition("]")
        port = rest[1:] if rest.startswith(":") else None
        return name + "]", port or None
    name, colon, port = host.rpartition(":")
    if colon and port.isdigit():
        return name, port
    return host, None


def host_is_allowed(host: str | None, allowed: frozenset[str]) -> bool:
    """Whether this server answers to `host`. See the module docstring."""
    if not host:
        return False
    host = host.strip().lower()
    name, port = _split_host(host)
    if name in LOOPBACK_HOSTS or name in allowed:
        return True
    return port is not None and host in allowed


def origin_is_allowed(origin: str | None, host: str | None, allowed: frozenset[str]) -> bool:
    """Whether a request bearing `origin` may proceed, once `host` has passed."""
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


def fetch_site_is_allowed(
    fetch_site: str | None, origin: str | None, allowed: frozenset[str]
) -> bool:
    """Whether the page that caused this request may cause it at all."""
    if (fetch_site or "").strip().lower() not in _FOREIGN_SITES:
        return True
    return origin is not None and origin.strip().rstrip("/") in allowed


def _refused(detail: str) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": detail})


class RequestGateMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        host = request.headers.get("host")
        if not host_is_allowed(host, configured_allowed_hosts()):
            return _refused(
                f"This server does not answer to the name {host!r}. It answers to "
                "localhost and 127.0.0.1; add another name to FACTOR_IA_ALLOWED_HOSTS "
                "if you reach it some other way."
            )
        api = request.url.path == "/api" or request.url.path.startswith("/api/")
        if not api and request.method.upper() not in MUTATING_METHODS:
            return await call_next(request)
        origin = request.headers.get("origin")
        allowed = configured_allowed_origins()
        if not origin_is_allowed(origin, host, allowed):
            return _refused(
                "Cross-origin request refused. This server only accepts API "
                "requests from its own pages."
            )
        if api and not fetch_site_is_allowed(
            request.headers.get("sec-fetch-site"), origin, allowed
        ):
            return _refused(
                "Request refused: another site's page asked for it. This server only "
                "accepts API requests from its own pages."
            )
        return await call_next(request)
