"""Command line: pynetflix <command>.  Run `pynetflix -h` for the list."""

import argparse
import csv
import json
import sys
from pathlib import Path

from . import __version__, db, stats
from .config import load_config

EXPORT_COLUMNS = ["profile", "watched_on", "watched_at", "title", "kind", "season_number",
                  "episode_number", "episode_title", "duration_seconds", "device", "year",
                  "netflix_status", "removed_from_netflix", "netflix_leaving_on"]


def _tmdb(cfg):
    if not cfg.tmdb_api_key:
        sys.exit("Set TMDB_API_KEY (free at https://www.themoviedb.org/settings/api)")
    from .tmdb import TMDB
    return TMDB(cfg.tmdb_api_key)


def _print(data) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="pynetflix", description="Your Netflix account as a local API.")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--db", help="database path (default ~/.pynetflix/pynetflix.db)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="what's in the database and what to do next")

    p = sub.add_parser("import", help="import a Viewing Activity CSV or the personal data export ZIP")
    p.add_argument("file", type=Path)
    p.add_argument("--profile", help="profile name (required for Viewing Activity CSVs)")
    p.add_argument("--date-format", help="strptime format of the CSV Date column, e.g. %%d/%%m/%%Y")

    sub.add_parser("login", help="log in to Netflix in a browser window and save the session")

    p = sub.add_parser("sync", help="pull the latest history from netflix.com (needs login)")
    p.add_argument("--profile", action="append", help="only these profiles (repeatable)")
    p.add_argument("--check-leaving", type=int, default=0, metavar="N",
                   help="also read 'Last day to watch' on the N most recently watched titles")
    p.add_argument("--show-browser", action="store_true")

    p = sub.add_parser("enrich", help="add genres, cast, runtimes from TMDB (needs TMDB_API_KEY)")
    p.add_argument("--refresh", action="store_true", help="re-fetch already enriched titles")
    p.add_argument("--limit", type=int)

    sub.add_parser("check-availability", help="re-check which watched titles are still on Netflix")

    p = sub.add_parser("watch", help="daily routine: sync (if logged in), check availability, report changes")
    p.add_argument("--no-sync", action="store_true", help="skip the netflix.com sync")

    p = sub.add_parser("removed", help="titles you watched that left Netflix")
    p.add_argument("--profile")

    sub.add_parser("leaving", help="watched titles with an announced last day on Netflix")

    p = sub.add_parser("history", help="print viewing history")
    p.add_argument("--profile"); p.add_argument("--since"); p.add_argument("--until")
    p.add_argument("--kind", choices=["movie", "series"]); p.add_argument("--search")
    p.add_argument("--limit", type=int, default=50)

    p = sub.add_parser("export", help="write the full history to CSV (includes removed_from_netflix)")
    p.add_argument("out", type=Path)
    p.add_argument("--profile")

    p = sub.add_parser("ratings", help="your thumbs up/down ratings (from the data export)")
    p.add_argument("--profile")
    p.add_argument("--rating", type=int, choices=[-1, 1, 2], help="-1 down, 1 up, 2 love this")

    p = sub.add_parser("wrapped", help="yearly/monthly recap")
    p.add_argument("--profile"); p.add_argument("--year", type=int); p.add_argument("--month", type=int)
    p.add_argument("--html", type=Path, help="write a shareable HTML page here")

    p = sub.add_parser("abandoned", help="shows you stopped watching")
    p.add_argument("--profile"); p.add_argument("--idle-days", type=int, default=60)

    sub.add_parser("shared", help="what profiles have in common + watch-together ideas")

    p = sub.add_parser("letterboxd", help="export movies as a Letterboxd import CSV")
    p.add_argument("out", type=Path); p.add_argument("--profile")

    p = sub.add_parser("trakt", help="sync history to Trakt")
    p.add_argument("action", choices=["login", "push"])
    p.add_argument("--profile"); p.add_argument("--since")

    p = sub.add_parser("serve", help="run the REST API")
    p.add_argument("--host", default="127.0.0.1"); p.add_argument("--port", type=int, default=8000)

    sub.add_parser("mcp", help="run the MCP server (stdio) for Claude and other assistants")

    args = parser.parse_args(argv)
    cfg = load_config()
    db_path = args.db or str(cfg.db_path)
    conn = db.connect(db_path)

    if args.cmd == "status":
        st = stats.status(conn)
        st["database"] = db_path
        st["logged_in"] = cfg.session_path.exists()
        _print(st)
        if not st["viewings"]:
            print("\nNext: pynetflix import <file>  or  pynetflix login && pynetflix sync")
        elif st["not_enriched"]:
            print("\nNext: pynetflix enrich  (needs TMDB_API_KEY)")

    elif args.cmd == "import":
        from . import importers
        if not args.file.exists():
            sys.exit(f"File not found: {args.file}")
        is_export = args.file.suffix.lower() == ".zip" or args.file.name == "ViewingActivity.csv"
        if is_export:
            result = importers.import_data_export(conn, args.file)
            rated = importers.import_ratings(conn, args.file)
            if rated:
                print(f"{rated} ratings imported")
        else:
            if not args.profile:
                sys.exit("Viewing Activity CSVs don't include the profile name: pass --profile NAME")
            result = importers.import_viewing_activity_csv(conn, args.file, args.profile, args.date_format)
        print(result)

    elif args.cmd == "login":
        from .sync import login
        login(cfg.session_path)

    elif args.cmd == "sync":
        from .sync import sync
        r = sync(conn, cfg.session_path, profiles=args.profile, check_leaving=args.check_leaving,
                 headless=not args.show_browser)
        print(f"{r.added} new, {r.merged} already known. Profiles: {', '.join(r.profiles)}")
        if args.check_leaving:
            print(f"{r.leaving_notices} 'last day to watch' notices found")

    elif args.cmd == "enrich":
        from .enrich import enrich_titles
        r = enrich_titles(conn, _tmdb(cfg), cfg.country, refresh=args.refresh, limit=args.limit)
        print(f"{r.enriched} enriched, {r.not_found} not found on TMDB, "
              f"{r.reclassified} re-classified as series")

    elif args.cmd == "check-availability":
        from .availability import check_availability
        r = check_availability(conn, _tmdb(cfg), cfg.country)
        print(f"Checked {r.checked} titles in {cfg.country}: {r.still_available} on Netflix, "
              f"{r.newly_removed} newly removed, {r.back_on_netflix} back on Netflix")

    elif args.cmd == "watch":
        from .availability import watch
        if not args.no_sync and cfg.session_path.exists():
            from .sync import sync
            try:
                r = sync(conn, cfg.session_path)
                print(f"Sync: {r.added} new viewings from {', '.join(r.profiles)}")
            except Exception as e:  # sync breaks when Netflix changes its site; keep watching
                print(f"Sync failed ({e}); continuing with availability check", file=sys.stderr)
        report = watch(conn, _tmdb(cfg), cfg.country)
        print(f"Checked {report['checked']} titles in {cfg.country}.")
        for heading, key, date_key in [("Newly left Netflix", "newly_left", "removed_from_netflix"),
                                       ("Back on Netflix", "back_on_netflix", None),
                                       ("Leaving soon", "leaving_soon", "netflix_leaving_on")]:
            print(f"\n{heading}: {len(report[key]) or 'none'}")
            for t in report[key]:
                print(f"  {t[date_key] + '  ' if date_key and t.get(date_key) else ''}{t['name']}")

    elif args.cmd == "removed":
        from .availability import removed_titles
        for t in removed_titles(conn, args.profile):
            print(f"{t['removed_from_netflix']}  {t['name']} ({t['kind']})  "
                  f"[{t['removed_precision']}; last watched {t['last_watched_on']}]")

    elif args.cmd == "leaving":
        from .availability import leaving_soon
        for t in leaving_soon(conn):
            print(f"{t['netflix_leaving_on']}  {t['name']}")

    elif args.cmd == "history":
        _print(stats.history(conn, profile=args.profile, since=args.since, until=args.until,
                             kind=args.kind, search=args.search, limit=args.limit))

    elif args.cmd == "export":
        rows = stats.history(conn, profile=args.profile, limit=10_000_000)
        with open(args.out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=EXPORT_COLUMNS, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        print(f"Wrote {len(rows)} rows to {args.out}")

    elif args.cmd == "wrapped":
        w = stats.wrapped(conn, profile=args.profile, year=args.year, month=args.month)
        if args.html:
            from .report import render_wrapped
            args.html.write_text(render_wrapped(w), encoding="utf-8")
            print(f"Wrote {args.html}")
        else:
            _print(w)

    elif args.cmd == "ratings":
        _print(stats.ratings(conn, profile=args.profile, rating=args.rating))

    elif args.cmd == "abandoned":
        _print(stats.abandoned(conn, profile=args.profile, idle_days=args.idle_days))

    elif args.cmd == "shared":
        _print(stats.shared(conn))

    elif args.cmd == "letterboxd":
        from .exporters import export_letterboxd
        print(f"Wrote {export_letterboxd(conn, args.out, args.profile)} movies to {args.out}")

    elif args.cmd == "trakt":
        from .exporters import Trakt
        if not (cfg.trakt_client_id and cfg.trakt_client_secret):
            sys.exit("Set TRAKT_CLIENT_ID and TRAKT_CLIENT_SECRET (create an app at https://trakt.tv/oauth/applications)")
        trakt = Trakt(cfg.trakt_client_id, cfg.trakt_client_secret, cfg.trakt_token_path)
        if args.action == "login":
            trakt.login()
            print("Trakt connected.")
        else:
            _print(trakt.push_history(conn, profile=args.profile, since=args.since))

    elif args.cmd == "serve":
        import uvicorn

        from .api import create_app
        uvicorn.run(create_app(db_path), host=args.host, port=args.port)

    elif args.cmd == "mcp":
        from .mcp_server import build_server
        build_server(db_path).run()


if __name__ == "__main__":
    main()
