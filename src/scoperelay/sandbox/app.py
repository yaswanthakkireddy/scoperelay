"""Explicitly gated loopback-only sandbox API; no production writer routes."""

import asyncio
from contextlib import asynccontextmanager, suppress
from hashlib import sha256
from pathlib import Path
import re
import secrets
import sqlite3
from urllib.parse import urlsplit

from fastapi import FastAPI, Header, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .service import SandboxRejected, SandboxService


_HERE = Path(__file__).resolve().parent


class RunInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str
    scenario_id: str
    ticket_text: str | None = Field(default=None, max_length=600)
    idempotency_key: str = Field(min_length=12, max_length=80)
    run_kind: str = "DETERMINISTIC_COMPONENT_TEST"


def create_sandbox_app(
    root: Path,
    *,
    enabled: bool = False,
    daily_turn_budget: int = 40,
    session_ttl: int = 1800,
    cleanup_interval: float = 60,
    invite_digest: str | None = None,
    local_live_enabled: bool = False,
) -> FastAPI:
    if invite_digest is not None and not re.fullmatch(r"[0-9a-f]{64}", invite_digest):
        raise ValueError("invite digest must be a lowercase SHA-256 hex string")
    service = SandboxService(
        root,
        enabled=enabled,
        daily_turn_budget=daily_turn_budget,
        session_ttl=session_ttl,
        beta_restricted=invite_digest is not None,
        local_live_enabled=local_live_enabled,
    )
    if cleanup_interval <= 0:
        raise ValueError("invalid cleanup interval")

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        async def cleanup_loop():
            while True:
                await asyncio.sleep(cleanup_interval)
                try:
                    await asyncio.to_thread(service.sweep_expired)
                except (OSError, sqlite3.Error):
                    # Expired credentials still fail owner checks. Retain data for
                    # a later retry; never silently delete the authoritative row.
                    pass

        task = asyncio.create_task(cleanup_loop()) if enabled else None
        try:
            yield
        finally:
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(
        title="ScopeRelay local sandbox prototype",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.sandbox = service
    app.mount(
        "/sandbox/static",
        StaticFiles(directory=str(_HERE / "static")),
        name="sandbox-static",
    )

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        host = request.headers.get("host", "")
        origin = request.headers.get("origin")
        try:
            parsed = urlsplit("http://" + host)
            valid_host = (
                bool(re.fullmatch(r"(?:127\.0\.0\.1|localhost)(?::[0-9]{1,5})?", host))
                and parsed.hostname in {"127.0.0.1", "localhost"}
                and (parsed.port is None or 1 <= parsed.port <= 65535)
            )
            valid_origin = origin is None or (
                urlsplit(origin).scheme == "http"
                and urlsplit(origin).netloc == host
                and urlsplit(origin).path in ("", "/")
                and not urlsplit(origin).query
                and not urlsplit(origin).fragment
            )
        except ValueError:
            valid_host = valid_origin = False
        if not valid_host:
            response = PlainTextResponse("LOCAL_HOST_REQUIRED", status_code=400)
        elif not valid_origin or request.headers.get("sec-fetch-site") == "cross-site":
            response = PlainTextResponse("CROSS_ORIGIN_REJECTED", status_code=403)
        elif request.method == "POST" and origin is None:
            response = PlainTextResponse("ORIGIN_REQUIRED", status_code=403)
        elif request.query_params:
            response = PlainTextResponse("QUERY_NOT_SUPPORTED", status_code=400)
        elif request.method not in {"GET", "HEAD", "POST"}:
            response = PlainTextResponse("METHOD_NOT_ALLOWED", status_code=405)
        elif (
            request.headers.get("content-length", "0").isdigit()
            and int(request.headers.get("content-length", "0")) > 4096
        ):
            response = PlainTextResponse("BODY_TOO_LARGE", status_code=413)
        else:
            if request.method == "POST":
                body = bytearray()
                too_large = False
                async for chunk in request.stream():
                    if len(body) + len(chunk) > 4096:
                        too_large = True
                        break
                    body.extend(chunk)
                if too_large:
                    response = PlainTextResponse("BODY_TOO_LARGE", status_code=413)
                else:
                    request._body = bytes(body)
                    response = await call_next(request)
            else:
                response = await call_next(request)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
                "img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
                "Cross-Origin-Resource-Policy": "same-origin",
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
            }
        )
        return response

    @app.exception_handler(SandboxRejected)
    def rejected(_: Request, exc: SandboxRejected):
        return JSONResponse({"code": exc.code}, status_code=exc.status)

    @app.exception_handler(sqlite3.Error)
    @app.exception_handler(OSError)
    def storage_failed(_: Request, __: Exception):
        return JSONResponse({"code": "SANDBOX_STORAGE_UNAVAILABLE"}, status_code=503)

    def client_key(request: Request) -> str:
        # Uvicorn must use proxy_headers=False. Never trust visitor-supplied
        # X-Forwarded-For; a reverse proxy's identity contract needs review.
        if request.client is None or not request.client.host:
            raise SandboxRejected("CLIENT_IDENTITY_UNAVAILABLE", 503)
        return sha256(request.client.host.encode("utf-8")).hexdigest()

    @app.get("/sandbox")
    def page():
        return FileResponse(_HERE / "static" / "playground.html")

    @app.get("/api/sandbox/capacity")
    def capacity():
        return {**service.capacity(), "invite_required": invite_digest is not None}

    @app.get("/api/sandbox/scenarios")
    def scenarios():
        return {
            "scenarios": service.scenarios(),
            "mode": "DETERMINISTIC_COMPONENT_TEST",
        }

    @app.post("/api/sandbox/session")
    def session(
        request: Request, x_scoperelay_invite: str | None = Header(default=None)
    ):
        if invite_digest is not None and (
            not x_scoperelay_invite
            or len(x_scoperelay_invite) > 128
            or not secrets.compare_digest(
                sha256(x_scoperelay_invite.encode("utf-8")).hexdigest(), invite_digest
            )
        ):
            raise SandboxRejected("INVITE_REQUIRED", 403)
        token, csrf = service.new_session(client_key(request))
        response = JSONResponse(
            {
                "csrf": csrf,
                "expires_in_seconds": service.session_ttl,
                "mode": "DETERMINISTIC_COMPONENT_TEST",
                "live": False,
            }
        )
        response.set_cookie(
            "sr_sandbox",
            token,
            httponly=True,
            samesite="strict",
            secure=False,
            max_age=service.session_ttl,
            path="/api/sandbox",
        )
        return response

    @app.get("/api/sandbox/session")
    def existing_session(request: Request):
        return {
            "csrf": service.refresh_csrf(request.cookies.get("sr_sandbox")),
            "mode": "DETERMINISTIC_COMPONENT_TEST",
            "live": False,
        }

    @app.post("/api/sandbox/runs")
    def start(
        request: Request,
        body: RunInput,
        x_scoperelay_csrf: str | None = Header(default=None),
    ):
        run_id, created = service.start(
            request.cookies.get("sr_sandbox"),
            x_scoperelay_csrf,
            scenario_id=body.scenario_id,
            mode=body.mode,
            ticket_text=body.ticket_text,
            idempotency_key=body.idempotency_key,
            client_key=client_key(request),
            run_kind=body.run_kind,
        )
        return JSONResponse(
            {
                "run_id": run_id,
                "created": created,
                "mode": body.run_kind
                if created
                else service.status(request.cookies.get("sr_sandbox"), run_id)["mode"],
                "model_invoked": service.status(
                    request.cookies.get("sr_sandbox"), run_id
                )["model_invoked"],
            },
            status_code=201 if created else 200,
        )

    @app.get("/api/sandbox/runs/{run_id}")
    def status(request: Request, run_id: str):
        return service.status(request.cookies.get("sr_sandbox"), run_id)

    @app.get("/api/sandbox/runs/{run_id}/evidence")
    def evidence(request: Request, run_id: str):
        return service.evidence(request.cookies.get("sr_sandbox"), run_id)

    @app.get("/health")
    def health():
        return {
            "service": "local-sandbox-prototype",
            "live": "LOCAL_OLLAMA_ONLY" if local_live_enabled else "DISABLED",
            "state": service.capacity()["sandbox"],
        }

    return app
