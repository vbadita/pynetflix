"""Track when titles you watched leave Netflix.

Nobody publishes a free, reliable "removed on" date, so we derive one:

* Every time you watched a title on Netflix proves it was available that day.
* Each availability check (TMDB / JustWatch data for your country) adds a dated observation.
* The first check that finds a title gone sets ``netflix_removed_detected_at``. The title was
  removed somewhere between ``netflix_last_seen_available`` and that date. Run checks
  regularly (e.g. a daily cron) and the window shrinks to a day.
* If a source gives an exact date (Netflix's own "Last day to watch" notice, captured
  during sync), it is stored in ``netflix_leaving_on`` and becomes ``netflix_removed_on`` once
  that date has passed and the title is gone.
"""

import sqlite3
from dataclasses import dataclass

from . import db
from .tmdb import TMDB, on_netflix


def _last_watched(conn: sqlite3.Connection, title_id: int) -> str | None:
    return conn.execute("SELECT MAX(watched_on) FROM viewings WHERE title_id = ?", (title_id,)).fetchone()[0]


def record_availability(conn: sqlite3.Connection, title_id: int, country: str, available: bool,
                        checked_on: str | None = None) -> None:
    checked_on = checked_on or db.today()
    conn.execute(
        "INSERT OR REPLACE INTO availability_log (title_id, checked_on, country, available) VALUES (?, ?, ?, ?)",
        (title_id, checked_on, country, int(available)),
    )
    row = conn.execute("SELECT * FROM titles WHERE id = ?", (title_id,)).fetchone()
    last_seen = max(filter(None, [row["netflix_last_seen_available"], _last_watched(conn, title_id)]),
                    default=None)

    if available:
        conn.execute(
            """UPDATE titles SET netflix_status = 'available', netflix_last_seen_available = ?,
                   netflix_removed_detected_at = NULL, netflix_removed_on = NULL,
                   availability_checked_at = ?, availability_country = ?
               WHERE id = ?""",
            (max(filter(None, [last_seen, checked_on])), checked_on, country, title_id),
        )
        return

    removed_on = row["netflix_removed_on"]
    leaving = row["netflix_leaving_on"]
    if not removed_on and leaving and leaving <= checked_on and (not last_seen or leaving >= last_seen):
        removed_on = leaving
    detected = row["netflix_removed_detected_at"] if row["netflix_status"] == "removed" else None
    conn.execute(
        """UPDATE titles SET netflix_status = 'removed', netflix_last_seen_available = ?,
               netflix_removed_detected_at = ?, netflix_removed_on = ?,
               availability_checked_at = ?, availability_country = ?
           WHERE id = ?""",
        (last_seen, detected or checked_on, removed_on, checked_on, country, title_id),
    )


def record_leaving_notice(conn: sqlite3.Connection, title_id: int, leaving_on: str) -> None:
    conn.execute("UPDATE titles SET netflix_leaving_on = ? WHERE id = ?", (leaving_on, title_id))


@dataclass
class AvailabilityResult:
    checked: int = 0
    still_available: int = 0
    newly_removed: int = 0
    back_on_netflix: int = 0


def check_availability(conn: sqlite3.Connection, tmdb: TMDB, country: str,
                       only_status: str | None = None) -> AvailabilityResult:
    """Re-check every enriched title against TMDB's Netflix listing for ``country``."""
    result = AvailabilityResult()
    query = "SELECT id, kind, tmdb_id, netflix_status FROM titles WHERE tmdb_id IS NOT NULL"
    params: tuple = ()
    if only_status:
        query += " AND netflix_status = ?"
        params = (only_status,)
    for row in conn.execute(query, params).fetchall():
        available = on_netflix(tmdb.watch_providers(row["tmdb_id"], row["kind"]), country)
        with conn:
            record_availability(conn, row["id"], country, available)
        result.checked += 1
        if available:
            result.still_available += 1
            if row["netflix_status"] == "removed":
                result.back_on_netflix += 1
        elif row["netflix_status"] != "removed":
            result.newly_removed += 1
    return result


def removed_titles(conn: sqlite3.Connection, profile: str | None = None) -> list[dict]:
    """Titles you watched that are no longer on Netflix, newest removal first."""
    query = "SELECT * FROM titles_view WHERE netflix_status = 'removed'"
    params: tuple = ()
    if profile:
        query += (" AND id IN (SELECT title_id FROM viewings v JOIN profiles p ON p.id = v.profile_id"
                  " WHERE p.name = ?)")
        params = (profile,)
    query += " ORDER BY removed_from_netflix DESC"
    return [db.row_to_dict(r) for r in conn.execute(query, params)]


def leaving_soon(conn: sqlite3.Connection) -> list[dict]:
    return [db.row_to_dict(r) for r in conn.execute(
        "SELECT * FROM titles_view WHERE netflix_leaving_on >= date('now') "
        "AND netflix_status != 'removed' ORDER BY netflix_leaving_on")]
