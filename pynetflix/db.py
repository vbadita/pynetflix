"""SQLite storage. One file, no server."""

import json
import sqlite3
from datetime import date
from pathlib import Path

from .parsing import ParsedTitle, normalize

SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    guid        TEXT
);

CREATE TABLE IF NOT EXISTS titles (
    id                  INTEGER PRIMARY KEY,
    name                TEXT NOT NULL,
    normalized_name     TEXT NOT NULL,
    kind                TEXT NOT NULL,           -- movie | series
    netflix_id          INTEGER,
    -- enrichment (TMDB)
    tmdb_id             INTEGER,
    year                INTEGER,
    genres              TEXT,                    -- JSON list
    cast_members        TEXT,                    -- JSON list
    runtime_minutes     INTEGER,
    overview            TEXT,
    poster_url          TEXT,
    total_seasons       INTEGER,
    total_episodes      INTEGER,
    last_air_date       TEXT,
    series_status       TEXT,                    -- e.g. "Returning Series", "Ended"
    enriched_at         TEXT,
    -- Netflix availability tracking
    netflix_status              TEXT NOT NULL DEFAULT 'unknown',  -- available | removed | unknown
    netflix_last_seen_available TEXT,            -- last date we know it was on Netflix
    netflix_removed_detected_at TEXT,            -- first check where it was gone
    netflix_removed_on          TEXT,            -- exact removal date, when a source provides it
    netflix_leaving_on          TEXT,            -- announced "last day to watch"
    availability_checked_at     TEXT,
    availability_country        TEXT,
    UNIQUE (normalized_name, kind)
);

CREATE TABLE IF NOT EXISTS viewings (
    id               INTEGER PRIMARY KEY,
    profile_id       INTEGER NOT NULL REFERENCES profiles(id),
    title_id         INTEGER NOT NULL REFERENCES titles(id),
    watched_on       TEXT NOT NULL,              -- YYYY-MM-DD
    watched_at       TEXT,                       -- full timestamp (UTC) when known
    duration_seconds INTEGER,
    season_label     TEXT,
    season_number    INTEGER,
    episode_title    TEXT,
    episode_number   INTEGER,
    device           TEXT,
    country          TEXT,
    source           TEXT NOT NULL,              -- csv | export | sync
    raw_title        TEXT NOT NULL,
    dedup_key        TEXT NOT NULL,
    UNIQUE (profile_id, dedup_key)
);

CREATE INDEX IF NOT EXISTS viewings_by_date ON viewings (profile_id, watched_on);
CREATE INDEX IF NOT EXISTS viewings_by_title ON viewings (title_id);

CREATE TABLE IF NOT EXISTS ratings (
    profile_id  INTEGER NOT NULL REFERENCES profiles(id),
    title_id    INTEGER NOT NULL REFERENCES titles(id),
    rating      INTEGER NOT NULL,                -- -1 thumbs down, 1 thumbs up, 2 love this
    rated_at    TEXT,
    PRIMARY KEY (profile_id, title_id)
);

-- Titles first guessed wrong ("Show: Episode" parsed as a movie) and later corrected.
CREATE TABLE IF NOT EXISTS title_aliases (
    normalized_name TEXT PRIMARY KEY,
    title_id        INTEGER NOT NULL REFERENCES titles(id)
);

CREATE TABLE IF NOT EXISTS availability_log (
    title_id    INTEGER NOT NULL REFERENCES titles(id),
    checked_on  TEXT NOT NULL,
    country     TEXT NOT NULL,
    available   INTEGER NOT NULL,
    PRIMARY KEY (title_id, checked_on, country)
);

-- Convenience view: one row per title with a single "removed from Netflix" column.
DROP VIEW IF EXISTS titles_view;
CREATE VIEW titles_view AS
SELECT
    t.*,
    COALESCE(t.netflix_removed_on, t.netflix_removed_detected_at) AS removed_from_netflix,
    CASE
        WHEN t.netflix_removed_on IS NOT NULL THEN 'exact'
        WHEN t.netflix_removed_detected_at IS NOT NULL THEN 'between ' ||
             COALESCE(t.netflix_last_seen_available, '?') || ' and ' || t.netflix_removed_detected_at
    END AS removed_precision,
    (SELECT MAX(v.watched_on) FROM viewings v WHERE v.title_id = t.id) AS last_watched_on,
    (SELECT COUNT(*) FROM viewings v WHERE v.title_id = t.id) AS view_count
FROM titles t;
"""


def connect(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def get_or_create_profile(conn: sqlite3.Connection, name: str, guid: str | None = None) -> int:
    row = conn.execute("SELECT id, guid FROM profiles WHERE name = ?", (name,)).fetchone()
    if row:
        if guid and not row["guid"]:
            conn.execute("UPDATE profiles SET guid = ? WHERE id = ?", (guid, row["id"]))
        return row["id"]
    return conn.execute("INSERT INTO profiles (name, guid) VALUES (?, ?)", (name, guid)).lastrowid


def get_or_create_title(conn: sqlite3.Connection, parsed: ParsedTitle,
                        netflix_id: int | None = None) -> int:
    key = normalize(parsed.name)
    if parsed.kind == "movie":
        alias = conn.execute("SELECT title_id FROM title_aliases WHERE normalized_name = ?",
                             (key,)).fetchone()
        if alias:
            show, _, episode = parsed.name.partition(": ")
            parsed.name, parsed.kind, parsed.episode_title = show, "series", episode
            return alias["title_id"]
    row = conn.execute(
        "SELECT id, netflix_id FROM titles WHERE normalized_name = ? AND kind = ?",
        (key, parsed.kind),
    ).fetchone()
    if row:
        if netflix_id and not row["netflix_id"]:
            conn.execute("UPDATE titles SET netflix_id = ? WHERE id = ?", (netflix_id, row["id"]))
        return row["id"]
    return conn.execute(
        "INSERT INTO titles (name, normalized_name, kind, netflix_id) VALUES (?, ?, ?, ?)",
        (parsed.name, key, parsed.kind, netflix_id),
    ).lastrowid


def merge_movie_into_series(conn: sqlite3.Connection, title_id: int) -> int | None:
    """Turn a wrongly parsed "Show: Episode" movie into an episode of the series "Show"."""
    row = conn.execute("SELECT name, normalized_name FROM titles WHERE id = ?", (title_id,)).fetchone()
    show, sep, episode = row["name"].partition(": ")
    if not sep:
        return None
    series = ParsedTitle(name=show, kind="series", episode_title=episode)
    series_id = get_or_create_title(conn, series)
    for v in conn.execute("SELECT * FROM viewings WHERE title_id = ?", (title_id,)).fetchall():
        key = dedup_key(series_id, series, v["watched_on"])
        clash = conn.execute("SELECT id FROM viewings WHERE profile_id = ? AND dedup_key = ?",
                             (v["profile_id"], key)).fetchone()
        if clash:
            conn.execute("DELETE FROM viewings WHERE id = ?", (v["id"],))
        else:
            conn.execute("UPDATE viewings SET title_id = ?, episode_title = ?, dedup_key = ? WHERE id = ?",
                         (series_id, episode, key, v["id"]))
    conn.execute("DELETE FROM availability_log WHERE title_id = ?", (title_id,))
    conn.execute("UPDATE OR IGNORE ratings SET title_id = ? WHERE title_id = ?", (series_id, title_id))
    conn.execute("DELETE FROM ratings WHERE title_id = ?", (title_id,))
    conn.execute("UPDATE title_aliases SET title_id = ? WHERE title_id = ?", (series_id, title_id))
    conn.execute("DELETE FROM titles WHERE id = ?", (title_id,))
    conn.execute("INSERT OR REPLACE INTO title_aliases (normalized_name, title_id) VALUES (?, ?)",
                 (row["normalized_name"], series_id))
    return series_id


def dedup_key(title_id: int, parsed: ParsedTitle, watched_on: str) -> str:
    episode = normalize(parsed.episode_title or "")
    season = parsed.season_number if parsed.season_number is not None else normalize(parsed.season_label or "")
    return f"{title_id}|{season}|{episode}|{watched_on}"


def upsert_viewing(conn: sqlite3.Connection, *, profile_id: int, title_id: int,
                   parsed: ParsedTitle, raw_title: str, watched_on: str, source: str,
                   watched_at: str | None = None, duration_seconds: int | None = None,
                   device: str | None = None, country: str | None = None) -> bool:
    """Insert a viewing, or merge richer details into an existing one. Returns True if new."""
    key = dedup_key(title_id, parsed, watched_on)
    existed = conn.execute(
        "SELECT 1 FROM viewings WHERE profile_id = ? AND dedup_key = ?", (profile_id, key)
    ).fetchone()
    conn.execute(
        """
        INSERT INTO viewings (profile_id, title_id, watched_on, watched_at, duration_seconds,
                              season_label, season_number, episode_title, episode_number,
                              device, country, source, raw_title, dedup_key)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (profile_id, dedup_key) DO UPDATE SET
            watched_at       = COALESCE(MIN(viewings.watched_at, excluded.watched_at),
                                        viewings.watched_at, excluded.watched_at),
            duration_seconds = MAX(COALESCE(viewings.duration_seconds, 0),
                                   COALESCE(excluded.duration_seconds, 0)),
            episode_number   = COALESCE(viewings.episode_number, excluded.episode_number),
            device           = COALESCE(viewings.device, excluded.device),
            country          = COALESCE(viewings.country, excluded.country)
        """,
        (profile_id, title_id, watched_on, watched_at, duration_seconds,
         parsed.season_label, parsed.season_number, parsed.episode_title, parsed.episode_number,
         device, country, source, raw_title, key),
    )
    return existed is None


def row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    for field in ("genres", "cast_members"):
        if d.get(field):
            d[field] = json.loads(d[field])
    return d


def today() -> str:
    return date.today().isoformat()
