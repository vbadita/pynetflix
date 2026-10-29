"""MCP server so assistants like Claude can answer questions about your Netflix history.

Add to Claude Code:     claude mcp add pynetflix -- pynetflix mcp
Add to Claude Desktop:  {"mcpServers": {"pynetflix": {"command": "pynetflix", "args": ["mcp"]}}}
"""

from . import db, stats
from .availability import leaving_soon, removed_titles
from .config import load_config

try:  # MCP Python SDK 2.x
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # 1.x
    from mcp.server.fastmcp import FastMCP as _Server


def build_server(db_path: str | None = None):
    conn = db.connect(db_path or load_config().db_path)
    server = _Server("pynetflix", instructions=(
        "Read-only access to the user's own Netflix viewing history across profiles, enriched with "
        "TMDB metadata. Dates are YYYY-MM-DD. 'removed_from_netflix' is when a title left Netflix "
        "in the user's country; it may be a detection date rather than the exact day."))

    @server.tool()
    def list_profiles() -> list[dict]:
        """List Netflix profiles with viewing counts and first/last watch dates."""
        return stats.profiles(conn)

    @server.tool()
    def viewing_history(profile: str | None = None, since: str | None = None, until: str | None = None,
                        kind: str | None = None, search: str | None = None, limit: int = 50) -> list[dict]:
        """Viewing history, newest first. kind is 'movie' or 'series'; search matches title or episode."""
        return stats.history(conn, profile=profile, since=since, until=until, kind=kind,
                             search=search, limit=min(limit, 500))

    @server.tool()
    def ratings(profile: str | None = None, rating: int | None = None) -> list[dict]:
        """Thumbs ratings: -1 thumbs down, 1 thumbs up, 2 love this. Filter by profile or rating."""
        return stats.ratings(conn, profile=profile, rating=rating)

    @server.tool()
    def wrapped(profile: str | None = None, year: int | None = None, month: int | None = None) -> dict:
        """Yearly or monthly recap: hours, top titles and genres, streaks, binges."""
        return stats.wrapped(conn, profile=profile, year=year, month=month)

    @server.tool()
    def abandoned_shows(profile: str | None = None, idle_days: int = 60) -> list[dict]:
        """Series started but not finished, flagging ones with a new season out."""
        return stats.abandoned(conn, profile=profile, idle_days=idle_days)

    @server.tool()
    def removed_from_netflix(profile: str | None = None) -> list[dict]:
        """Titles the user watched that have since left Netflix, with when they left."""
        return removed_titles(conn, profile)

    @server.tool()
    def leaving_netflix_soon() -> list[dict]:
        """Watched titles with an announced last day on Netflix."""
        return leaving_soon(conn)

    @server.tool()
    def shared_profiles() -> dict:
        """Overlap between profiles and suggestions to watch together."""
        return stats.shared(conn)

    return server


def main() -> None:
    build_server().run()
