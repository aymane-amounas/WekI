"""Local reader: serves ZIM pages as-is with our chrome injected, and overlay pages cleaned."""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote, urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from starlette.middleware.trustedhost import TrustedHostMiddleware

from weki.config import Config
from weki.overlay import OverlayStore
from weki.resolver import Resolved, Resolver, normalize_path
from weki.sync.client import WikiClient
from weki.sync.worker import SyncWorker
from weki.zim import ZimReader

HERE = Path(__file__).resolve().parent
LOCAL_HOSTS = ["127.0.0.1", "localhost", "testserver"]
HEAD_END = re.compile(rb"</head\s*>", re.I)
BODY_START = re.compile(rb"<body\b[^>]*>", re.I)
BODY_END = re.compile(rb"</body\s*>", re.I)
STRICT_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self'; connect-src 'self'; base-uri 'none'; "
    "form-action 'self'; frame-ancestors 'none'"
)
ZIM_STYLES = (
    "_mw_/mediawiki.skinning.content.parsoid.css",
    "_mw_/ext.cite.parsoid.styles.css",
    "_mw_/ext.cite.styles.css",
    "_mw_/ext.math.styles.css",
    "_mw_/site.styles.css",
)


def wiki_url(path: str) -> str:
    return "/wiki/" + quote(path, safe="/:@,;()'!*")


def inject(page: bytes, *, head: str, top: str, bottom: str) -> bytes:
    """Add our chrome to a ZIM page without touching its content."""
    page = HEAD_END.sub(lambda m: head.encode() + m.group(0), page, count=1)
    page = BODY_START.sub(lambda m: m.group(0) + top.encode(), page, count=1)
    return BODY_END.sub(lambda m: bottom.encode() + m.group(0), page, count=1)


def open_zim(path: Path | None) -> ZimReader | None:
    if path is None or not path.exists():
        return None
    return ZimReader(path)


def create_app(config: Config) -> FastAPI:
    zim = open_zim(config.zim_path)
    overlay = OverlayStore(config.overlay_path, config.journal_mode)
    resolver = Resolver(zim, overlay)
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.filters["wiki_url"] = wiki_url
    zim_date = zim.metadata("Date") if zim else None
    zim_styles = [wiki_url(style) for style in ZIM_STYLES if zim and zim.has(style)]
    tasks: set[asyncio.Task] = set()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        client = WikiClient(
            config.wiki_domain,
            config.sync.user_agent,
            concurrency=config.sync.concurrency,
            delay=config.sync.delay_seconds,
        )
        app.state.worker = SyncWorker(
            config.sync,
            overlay,
            client,
            zim_date=zim.metadata("Date") if zim else None,
            nopic=config.mode == "nopic",
        )
        yield
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await client.aclose()
        overlay.close()

    app = FastAPI(title="WekI", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=LOCAL_HOSTS)
    app.mount("/assets", StaticFiles(directory=HERE.parent / "assets"), name="assets")
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    app.state.overlay = overlay
    app.state.resolver = resolver

    def live_url(path: str) -> str:
        return f"https://{config.wiki_domain}{wiki_url(path)}"

    def render(
        request: Request, name: str, status_code: int = 200, **context: object
    ) -> HTMLResponse:
        response = templates.TemplateResponse(
            request,
            name,
            {"has_zim": zim is not None, "wiki_domain": config.wiki_domain, **context},
            status_code=status_code,
        )
        response.headers["Content-Security-Policy"] = STRICT_CSP
        return response

    def history_url(path: str) -> str:
        return f"https://{config.wiki_domain}/w/index.php?title={quote(path)}&action=history"

    def zim_page(found: Resolved) -> HTMLResponse:
        partial = templates.env.get_template
        return HTMLResponse(
            inject(
                found.item.content,
                head=partial("_head.html").render(),
                top=partial("_topbar.html").render(),
                bottom=partial("_footer.html").render(
                    live_url=live_url(found.path), history_url=history_url(found.path)
                ),
            )
        )

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> HTMLResponse:
        return render(
            request,
            "home.html",
            recent=overlay.recent_history(),
            main_path=zim.main_path if zim else None,
            article_count=zim.article_count if zim else 0,
            snapshot=zim.metadata("Date") if zim else None,
            title="WekI",
        )

    @app.get("/wiki/{path:path}")
    def wiki(path: str, request: Request) -> Response:
        found = resolver.open(path)
        if found.kind in ("deleted", "missing"):
            code = 410 if found.kind == "deleted" else 404
            return render(
                request,
                "removed.html" if found.kind == "deleted" else "missing.html",
                code,
                title=found.title,
                path=found.path,
                deleted=found.deleted,
                live_url=live_url(found.path),
            )
        if found.path != normalize_path(path):
            return RedirectResponse(wiki_url(found.path), status_code=302)
        if found.kind == "zim" and not found.is_html:
            cache = {"Cache-Control": "public, max-age=86400"}
            return Response(found.item.content, media_type=found.item.mimetype, headers=cache)
        if found.kind == "zim":
            return zim_page(found)
        return render(
            request,
            "article.html",
            title=found.title,
            path=found.path,
            article=found.article,
            zim_styles=zim_styles,
            newer=not zim_date or found.article.rev_ts[:10] > zim_date,
            body=Markup(found.article.html),
            live_url=live_url(found.path),
            history_url=(
                f"https://{config.wiki_domain}/w/index.php?title={quote(found.path)}&action=history"
            ),
        )

    @app.get("/search", response_class=HTMLResponse)
    def search(request: Request, q: str = "") -> HTMLResponse:
        hits = resolver.search(q) if q.strip() else []
        return render(request, "search.html", title=f"{q} search", q=q, hits=hits)

    @app.get("/suggest")
    def suggest(q: str = "") -> JSONResponse:
        return JSONResponse([{"path": path, "title": title} for path, title in resolver.suggest(q)])

    def status_data() -> dict:
        return {
            **app.state.worker.status_dict(),
            **overlay.counts(),
            "zim_file": zim.path.name if zim else None,
            "zim_date": zim.metadata("Date") if zim else None,
            "zim_articles": zim.article_count if zim else 0,
            "wiki_domain": config.wiki_domain,
        }

    @app.get("/status", response_class=HTMLResponse)
    def status(request: Request) -> HTMLResponse:
        return render(request, "status.html", title="Sync status", status=status_data())

    @app.get("/status.json")
    def status_json() -> JSONResponse:
        return JSONResponse(status_data())

    @app.post("/sync")
    async def sync(request: Request) -> JSONResponse:
        origin = request.headers.get("origin")
        if origin and urlsplit(origin).netloc != request.headers.get("host"):
            return JSONResponse({"error": "cross-origin request refused"}, status_code=403)
        worker = app.state.worker
        started = not worker.status.running
        if started:
            task = asyncio.create_task(worker.run())
            tasks.add(task)
            task.add_done_callback(tasks.discard)
        return JSONResponse({"started": started}, status_code=202)

    return app
