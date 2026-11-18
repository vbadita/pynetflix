"""Fill in genres, cast, runtime and season counts from TMDB, and track Netflix availability."""

import json
import sqlite3
from collections.abc import Container
from dataclasses import dataclass

from . import db
from .availability import record_availability
from .parsing import normalize
from .tmdb import IMAGE_BASE, TMDB, on_netflix


@dataclass
class EnrichResult:
    enriched: int = 0
    not_found: int = 0
    reclassified: int = 0
    episodes_filled: int = 0


def _apply_details(conn: sqlite3.Connection, title_id: int, kind: str, d: dict) -> None:
    if kind == "movie":
        year = (d.get("release_date") or "")[:4]
        runtime = d.get("runtime")
        seasons = episodes = last_air = status = None
    else:
        year = (d.get("first_air_date") or "")[:4]
        runtimes = d.get("episode_run_time") or []
        runtime = runtimes[0] if runtimes else None
        seasons = d.get("number_of_seasons")
        episodes = d.get("number_of_episodes")
        last_air = d.get("last_air_date")
        status = d.get("status")
    cast = [c["name"] for c in (d.get("credits") or {}).get("cast", [])[:10]]
    conn.execute(
        """UPDATE titles SET tmdb_id = ?, year = ?, genres = ?, cast_members = ?, runtime_minutes = ?,
               overview = ?, poster_url = ?, total_seasons = ?, total_episodes = ?,
               last_air_date = ?, series_status = ?, enriched_at = ?
           WHERE id = ?""",
        (d["id"], int(year) if year else None, json.dumps([g["name"] for g in d.get("genres", [])]),
         json.dumps(cast), runtime, d.get("overview"),
         IMAGE_BASE + d["poster_path"] if d.get("poster_path") else None,
         seasons, episodes, last_air, status, db.today(), title_id),
    )


def fill_episode_numbers(conn: sqlite3.Connection, tmdb: TMDB, title_id: int, tmdb_id: int) -> int:
    """CSV exports only have episode names; look the numbers up per season on TMDB."""
    seasons = [r[0] for r in conn.execute(
        "SELECT DISTINCT season_number FROM viewings WHERE title_id = ? AND season_number IS NOT NULL "
        "AND episode_number IS NULL", (title_id,))]
    filled = 0
    for season_number in seasons:
        try:
            episodes = tmdb.season(tmdb_id, season_number).get("episodes", [])
        except Exception:
            continue  # Netflix "Part 2" may not map to a TMDB season number
        by_name = {normalize(e["name"]): e["episode_number"] for e in episodes if e.get("name")}
        for v in conn.execute("SELECT id, episode_title FROM viewings WHERE title_id = ? AND season_number = ? "
                              "AND episode_number IS NULL", (title_id, season_number)).fetchall():
            number = by_name.get(normalize(v["episode_title"] or ""))
            if number:
                conn.execute("UPDATE viewings SET episode_number = ? WHERE id = ?", (number, v["id"]))
                filled += 1
    return filled


def enrich_titles(conn: sqlite3.Connection, tmdb: TMDB, country: str,
                  refresh: bool = False, limit: int | None = None) -> EnrichResult:
    result = EnrichResult()
    done: set[int] = set()
    query = "SELECT id, name, kind, tmdb_id FROM titles"
    if not refresh:
        query += " WHERE enriched_at IS NULL"
    if limit:
        query += f" LIMIT {int(limit)}"
    for row in conn.execute(query).fetchall():
        with conn:
            title_id, kind = row["id"], row["kind"]
            tmdb_id = row["tmdb_id"]
            if not tmdb_id:
                hit = tmdb.search(row["name"], kind)
                if hit is None and kind == "movie":
                    series_id = db.merge_movie_into_series(conn, title_id)
                    if series_id:
                        result.reclassified += 1
                        hit = tmdb.search(conn.execute("SELECT name FROM titles WHERE id = ?",
                                                       (series_id,)).fetchone()["name"], "series")
                        title_id, kind = series_id, "series"
                if hit is None:
                    conn.execute("UPDATE titles SET enriched_at = ? WHERE id = ?", (db.today(), title_id))
                    result.not_found += 1
                    continue
                tmdb_id = hit["id"]
            details = tmdb.details(tmdb_id, kind)
            _apply_details(conn, title_id, kind, details)
            done.add(title_id)
            if kind == "series":
                result.episodes_filled += fill_episode_numbers(conn, tmdb, title_id, tmdb_id)
            record_availability(conn, title_id, country,
                                on_netflix(details.get("watch/providers", {}).get("results", {}), country))
            result.enriched += 1
    if not refresh:
        result.episodes_filled += backfill_episode_numbers(conn, tmdb, skip=done)
    return result


def backfill_episode_numbers(conn: sqlite3.Connection, tmdb: TMDB, skip: Container[int] = ()) -> int:
    """Fill episode numbers for viewings added after their series was enriched.

    A later CSV import or sync adds new episodes of a show that is already enriched, so the
    main enrich pass skips it and those viewings would never get a number (and Trakt would skip
    them). This only touches series that still have viewings without an episode number.
    """
    filled = 0
    for row in conn.execute(
            "SELECT DISTINCT t.id, t.tmdb_id FROM titles t JOIN viewings v ON v.title_id = t.id "
            "WHERE t.kind = 'series' AND t.tmdb_id IS NOT NULL AND t.enriched_at IS NOT NULL "
            "AND v.season_number IS NOT NULL AND v.episode_number IS NULL").fetchall():
        if row["id"] in skip:
            continue  # just enriched in this run; its seasons were already looked up
        with conn:
            filled += fill_episode_numbers(conn, tmdb, row["id"], row["tmdb_id"])
    return filled
