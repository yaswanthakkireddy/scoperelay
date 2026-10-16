"""Loopback-only, read-only synthetic evidence workbench."""

from pathlib import Path
from urllib.parse import urlsplit
import re

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import read_models
from scoperelay.storage.schema import SCHEMA_VERSION


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
_HERE = Path(__file__).resolve().parent
_TEMPLATES = Jinja2Templates(directory=str(_HERE / "templates"))


def _identifier(value: str) -> str:
    if not _ID.fullmatch(value):
        raise HTTPException(404, "Unknown evidence identifier")
    return value


def create_app(database_path: Path) -> FastAPI:
    """Only an application-owned fixed path is accepted; URLs cannot select DBs."""
    path = database_path.resolve(strict=True)
    if not path.is_file():
        raise FileNotFoundError(path)
    # Refuse silently creating a new file or serving an incompatible schema.
    with read_models._reader(path) as con:
        if con.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
            raise RuntimeError("unsupported workbench database schema")
        if con.execute("SELECT count(*) FROM database_identity").fetchone()[0] != 1:
            raise RuntimeError("database identity unavailable")

    app = FastAPI(
        title="ScopeRelay reviewer workbench",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")

    @app.middleware("http")
    async def local_read_only(request: Request, call_next):
        host = request.headers.get("host", "")
        origin = request.headers.get("origin")
        try:
            parsed = urlsplit("http://" + host)
            port = parsed.port
            local_host = (
                bool(re.fullmatch(r"(?:127\.0\.0\.1|localhost)(?::[0-9]{1,5})?", host))
                and parsed.hostname in {"127.0.0.1", "localhost"}
                and not parsed.username
                and not parsed.password
                and parsed.path in ("", "/")
                and "," not in host
                and (port is None or 1 <= port <= 65535)
            )
            same_origin = origin is None or (
                urlsplit(origin).scheme == "http"
                and urlsplit(origin).netloc == host
                and urlsplit(origin).path in ("", "/")
                and not urlsplit(origin).query
                and not urlsplit(origin).fragment
            )
        except ValueError:
            local_host, same_origin = False, False
        if not local_host:
            response = PlainTextResponse("Local host required", status_code=400)
        elif not same_origin:
            response = PlainTextResponse(
                "Cross-origin request rejected", status_code=403
            )
        elif request.headers.get("sec-fetch-site") == "cross-site":
            response = PlainTextResponse("Cross-site request rejected", status_code=403)
        elif request.query_params:
            response = PlainTextResponse("Unexpected query parameter", status_code=400)
        elif request.method not in {"GET", "HEAD"}:
            response = PlainTextResponse("Read-only workbench", status_code=405)
        else:
            response = await call_next(request)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "Content-Security-Policy": "default-src 'none'; style-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
                "Cross-Origin-Resource-Policy": "same-origin",
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
                "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
            }
        )
        return response

    def render(
        request: Request, template: str, *, title: str, section: str, **data
    ) -> HTMLResponse:
        return _TEMPLATES.TemplateResponse(
            request,
            template,
            {
                "title": title,
                "section": section,
                "db": path.name,
                **data,
            },
        )

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        return render(
            request,
            "overview.html",
            title="Overview",
            section="overview",
            overview=read_models.overview(path),
        )

    @app.get("/scenarios", response_class=HTMLResponse)
    def scenario_list(request: Request):
        offline = read_models.offline_evaluation(path)
        return render(
            request,
            "scenarios.html",
            title="Scenario explorer",
            section="scenarios",
            scenarios=read_models.scenarios(path),
            offline_cases={row["scenario_id"]: row for row in offline["cases"]},
        )

    @app.get("/evaluation", response_class=HTMLResponse)
    def evaluation(request: Request):
        return render(
            request,
            "evaluation.html",
            title="Offline evaluation",
            section="evaluation",
            evaluation=read_models.offline_evaluation(path),
        )

    @app.get("/scenarios/{scenario_id}", response_class=HTMLResponse)
    def scenario_detail(request: Request, scenario_id: str):
        record = read_models.scenario(path, _identifier(scenario_id))
        if record is None:
            raise HTTPException(404, "Scenario not found")
        offline = read_models.offline_evaluation(path)
        return render(
            request,
            "scenario.html",
            title=scenario_id,
            section="scenarios",
            scenario=record,
            offline=next(
                (row for row in offline["cases"] if row["scenario_id"] == scenario_id),
                None,
            ),
        )

    @app.get("/episodes", response_class=HTMLResponse)
    def episode_list(request: Request):
        return render(
            request,
            "episodes.html",
            title="Episode evidence",
            section="episodes",
            episodes=read_models.episodes(path),
        )

    @app.get("/episodes/{episode_id}", response_class=HTMLResponse)
    def episode_detail(request: Request, episode_id: str):
        record = read_models.episode(path, _identifier(episode_id))
        if record is None:
            raise HTTPException(404, "Episode not found")
        return render(
            request,
            "episode.html",
            title=episode_id,
            section="episodes",
            episode=record,
            decisions=read_models.DECISIONS,
        )

    @app.get("/resource", response_class=HTMLResponse)
    def resource(request: Request):
        return render(
            request,
            "resource.html",
            title="Protected resource",
            section="resource",
            history=read_models.resource_history(path),
            outcomes=read_models.RESOURCE_OUTCOMES,
        )

    @app.get("/recovery", response_class=HTMLResponse)
    def recovery(request: Request):
        return render(
            request,
            "recovery.html",
            title="Recovery & approvals",
            section="recovery",
            recovery=read_models.recovery_status(path),
        )

    return app
