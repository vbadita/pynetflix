"""Local REST API over your Netflix data.  Run with: pynetflix serve"""

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

from . import __version__, db, stats
from .availability import leaving_soon, removed_titles
from .config import load_config
from .report import render_wrapped


def create_app(db_path: str | None = None) -> FastAPI:
    conn = db.connect(db_path or load_config().db_path)
    app = FastAPI(title="pynetflix", version=__version__,
                  description="Your Netflix viewing history as a local API.")

    @app.get("/profiles")
    def get_profiles():
        return stats.profiles(conn)

    @app.get("/history")
    def get_history(profile: str | None = None, since: str | None = None, until: str | None = None,
                    kind: str | None = Query(None, pattern="^(movie|series)$"), q: str | None = None,
                    limit: int = Query(100, le=1000), offset: int = 0):
        return stats.history(conn, profile=profile, since=since, until=until, kind=kind,
                             search=q, limit=limit, offset=offset)

    @app.get("/profiles/{profile}/history")
    def get_profile_history(profile: str, since: str | None = None, until: str | None = None,
                            limit: int = Query(100, le=1000), offset: int = 0):
        return stats.history(conn, profile=profile, since=since, until=until, limit=limit, offset=offset)

    @app.get("/titles")
    def get_titles(status: str | None = Query(None, pattern="^(available|removed|unknown)$"),
                   kind: str | None = Query(None, pattern="^(movie|series)$"),
                   limit: int = Query(200, le=2000), offset: int = 0):
        return stats.titles(conn, status=status, kind=kind, limit=limit, offset=offset)

    @app.get("/titles/removed")
    def get_removed(profile: str | None = None):
        return removed_titles(conn, profile)

    @app.get("/titles/leaving")
    def get_leaving():
        return leaving_soon(conn)

    @app.get("/titles/{title_id}")
    def get_title(title_id: int):
        t = stats.title(conn, title_id)
        if not t:
            raise HTTPException(404, "Title not found")
        return t

    @app.get("/wrapped")
    def get_wrapped(profile: str | None = None, year: int | None = None,
                    month: int | None = Query(None, ge=1, le=12)):
        return stats.wrapped(conn, profile=profile, year=year, month=month)

    @app.get("/wrapped.html", response_class=HTMLResponse)
    def get_wrapped_html(profile: str | None = None, year: int | None = None,
                         month: int | None = Query(None, ge=1, le=12)):
        return render_wrapped(stats.wrapped(conn, profile=profile, year=year, month=month))

    @app.get("/abandoned")
    def get_abandoned(profile: str | None = None, idle_days: int = 60):
        return stats.abandoned(conn, profile=profile, idle_days=idle_days)

    @app.get("/shared")
    def get_shared():
        return stats.shared(conn)

    return app
