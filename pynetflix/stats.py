"""Queries and insights over the local database."""

import json
import sqlite3
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from . import db


def profiles(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute(
        """SELECT p.id, p.name, COUNT(v.id) AS viewings, MIN(v.watched_on) AS first_watched,
                  MAX(v.watched_on) AS last_watched
           FROM profiles p LEFT JOIN viewings v ON v.profile_id = p.id
           GROUP BY p.id ORDER BY p.name""")]


def status(conn: sqlite3.Connection) -> dict:
    """Overview of what's in the database and what still needs doing."""
    one = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    return {
        "profiles": one("SELECT COUNT(*) FROM profiles"),
        "viewings": one("SELECT COUNT(*) FROM viewings"),
        "first_watched": one("SELECT MIN(watched_on) FROM viewings"),
        "last_watched": one("SELECT MAX(watched_on) FROM viewings"),
        "titles": one("SELECT COUNT(*) FROM titles"),
        "not_enriched": one("SELECT COUNT(*) FROM titles WHERE enriched_at IS NULL"),
        "not_found_on_tmdb": one("SELECT COUNT(*) FROM titles WHERE enriched_at IS NOT NULL AND tmdb_id IS NULL"),
        "on_netflix": one("SELECT COUNT(*) FROM titles WHERE netflix_status = 'available'"),
        "removed_from_netflix": one("SELECT COUNT(*) FROM titles WHERE netflix_status = 'removed'"),
        "last_availability_check": one("SELECT MAX(availability_checked_at) FROM titles"),
        "ratings": one("SELECT COUNT(*) FROM ratings"),
        "sources": {r[0]: r[1] for r in conn.execute("SELECT source, COUNT(*) FROM viewings GROUP BY source")},
    }


def history(conn: sqlite3.Connection, profile: str | None = None, since: str | None = None,
            until: str | None = None, kind: str | None = None, search: str | None = None,
            limit: int = 100, offset: int = 0) -> list[dict]:
    where, params = [], []
    if profile:
        where.append("p.name = ?"); params.append(profile)
    if since:
        where.append("v.watched_on >= ?"); params.append(since)
    if until:
        where.append("v.watched_on <= ?"); params.append(until)
    if kind:
        where.append("t.kind = ?"); params.append(kind)
    if search:
        where.append("(t.name LIKE ? OR v.episode_title LIKE ?)"); params += [f"%{search}%"] * 2
    sql = f"""
        SELECT v.id, p.name AS profile, v.watched_on, v.watched_at, v.duration_seconds,
               t.id AS title_id, t.name AS title, t.kind, v.season_number, v.season_label,
               v.episode_title, v.episode_number, v.device, t.genres, t.year,
               t.netflix_status, COALESCE(t.netflix_removed_on, t.netflix_removed_detected_at)
                   AS removed_from_netflix, t.netflix_leaving_on, r.rating
        FROM viewings v JOIN profiles p ON p.id = v.profile_id JOIN titles t ON t.id = v.title_id
        LEFT JOIN ratings r ON r.profile_id = v.profile_id AND r.title_id = v.title_id
        {"WHERE " + " AND ".join(where) if where else ""}
        ORDER BY v.watched_on DESC, v.watched_at DESC, v.id DESC
        LIMIT ? OFFSET ?"""
    return [db.row_to_dict(r) for r in conn.execute(sql, params + [limit, offset])]


def ratings(conn: sqlite3.Connection, profile: str | None = None, rating: int | None = None) -> list[dict]:
    where, params = [], []
    if profile:
        where.append("p.name = ?"); params.append(profile)
    if rating is not None:
        where.append("r.rating = ?"); params.append(rating)
    sql = f"""SELECT p.name AS profile, t.id AS title_id, t.name AS title, t.kind, r.rating, r.rated_at,
                     t.netflix_status
              FROM ratings r JOIN profiles p ON p.id = r.profile_id JOIN titles t ON t.id = r.title_id
              {"WHERE " + " AND ".join(where) if where else ""}
              ORDER BY r.rated_at DESC"""
    return [dict(r) for r in conn.execute(sql, params)]


def title(conn: sqlite3.Connection, title_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM titles_view WHERE id = ?", (title_id,)).fetchone()
    if not row:
        return None
    d = db.row_to_dict(row)
    d["availability_log"] = [dict(r) for r in conn.execute(
        "SELECT checked_on, country, available FROM availability_log WHERE title_id = ? ORDER BY checked_on",
        (title_id,))]
    return d


def titles(conn: sqlite3.Connection, status: str | None = None, kind: str | None = None,
           limit: int = 200, offset: int = 0) -> list[dict]:
    where, params = [], []
    if status:
        where.append("netflix_status = ?"); params.append(status)
    if kind:
        where.append("kind = ?"); params.append(kind)
    sql = (f"SELECT * FROM titles_view {'WHERE ' + ' AND '.join(where) if where else ''} "
           "ORDER BY last_watched_on DESC LIMIT ? OFFSET ?")
    return [db.row_to_dict(r) for r in conn.execute(sql, params + [limit, offset])]


def _estimated_seconds(row: sqlite3.Row) -> int:
    """Real duration when we have it, otherwise the TMDB runtime, otherwise a guess."""
    if row["duration_seconds"]:
        return row["duration_seconds"]
    if row["runtime_minutes"]:
        return row["runtime_minutes"] * 60
    return 45 * 60 if row["kind"] == "series" else 100 * 60


def wrapped(conn: sqlite3.Connection, profile: str | None = None, year: int | None = None,
            month: int | None = None) -> dict:
    """Spotify-Wrapped style recap for a year (or a single month)."""
    year = year or date.today().year
    start = date(year, month or 1, 1)
    end = (date(year + (month == 12), (month % 12) + 1, 1) if month else date(year + 1, 1, 1)) - timedelta(days=1)
    params: list = [start.isoformat(), end.isoformat()]
    profile_sql = ""
    if profile:
        profile_sql = " AND p.name = ?"
        params.append(profile)
    rows = conn.execute(
        f"""SELECT v.*, t.name AS title, t.kind, t.genres, t.runtime_minutes, t.netflix_status
            FROM viewings v JOIN titles t ON t.id = v.title_id JOIN profiles p ON p.id = v.profile_id
            WHERE v.watched_on BETWEEN ? AND ?{profile_sql}""", params).fetchall()

    seconds_by_title: Counter = Counter()
    kind_by_title: dict[str, str] = {}
    genres: Counter = Counter()
    days: Counter = Counter()
    weekdays: Counter = Counter()
    hours: Counter = Counter()
    episodes_per_day: Counter = Counter()
    total = 0
    for r in rows:
        secs = _estimated_seconds(r)
        total += secs
        seconds_by_title[r["title"]] += secs
        kind_by_title[r["title"]] = r["kind"]
        for g in json.loads(r["genres"] or "[]"):
            genres[g] += secs
        days[r["watched_on"]] += secs
        weekdays[date.fromisoformat(r["watched_on"]).strftime("%A")] += secs
        if r["watched_at"]:
            hours[datetime.fromisoformat(r["watched_at"].rstrip("Z")).hour] += 1
        if r["kind"] == "series":
            episodes_per_day[(r["watched_on"], r["title"])] += 1

    # Longest run of consecutive days with something watched.
    streak = best = 0
    prev = None
    for d in sorted(days):
        cur = date.fromisoformat(d)
        streak = streak + 1 if prev and cur - prev == timedelta(days=1) else 1
        best = max(best, streak)
        prev = cur

    biggest_binge = max(episodes_per_day.items(), key=lambda kv: kv[1], default=None)
    late_night = sum(c for h, c in hours.items() if h >= 23 or h < 4)
    return {
        "period": {"from": start.isoformat(), "to": end.isoformat()},
        "profile": profile,
        "total_hours": round(total / 3600, 1),
        "viewings": len(rows),
        "titles": len(seconds_by_title),
        "movies": sum(1 for k in kind_by_title.values() if k == "movie"),
        "series": sum(1 for k in kind_by_title.values() if k == "series"),
        "top_titles": [{"title": t, "hours": round(s / 3600, 1), "kind": kind_by_title[t]}
                       for t, s in seconds_by_title.most_common(10)],
        "top_genres": [{"genre": g, "hours": round(s / 3600, 1)} for g, s in genres.most_common(5)],
        "busiest_day": ({"date": days.most_common(1)[0][0],
                         "hours": round(days.most_common(1)[0][1] / 3600, 1)} if days else None),
        "favourite_weekday": weekdays.most_common(1)[0][0] if weekdays else None,
        "longest_streak_days": best,
        "biggest_binge": ({"date": biggest_binge[0][0], "title": biggest_binge[0][1],
                           "episodes": biggest_binge[1]} if biggest_binge else None),
        "night_owl_share": round(late_night / sum(hours.values()), 2) if hours else None,
        "watched_then_removed": sorted({r["title"] for r in rows if r["netflix_status"] == "removed"}),
        "duration_is_estimated": any(not r["duration_seconds"] for r in rows),
    }


def abandoned(conn: sqlite3.Connection, profile: str | None = None, idle_days: int = 60) -> list[dict]:
    """Series you started but stopped, plus whether there's more to watch now."""
    params: list = []
    profile_sql = ""
    if profile:
        profile_sql = "AND p.name = ?"
        params.append(profile)
    rows = conn.execute(
        f"""SELECT t.id, t.name, p.name AS profile, t.total_seasons, t.total_episodes, t.series_status,
                   t.last_air_date, t.netflix_status,
                   COUNT(DISTINCT v.dedup_key) AS episodes_watched,
                   MAX(v.season_number) AS last_season_watched, MAX(v.watched_on) AS last_watched_on
            FROM viewings v JOIN titles t ON t.id = v.title_id JOIN profiles p ON p.id = v.profile_id
            WHERE t.kind = 'series' {profile_sql}
            GROUP BY t.id, p.id""", params).fetchall()
    cutoff = (date.today() - timedelta(days=idle_days)).isoformat()
    out = []
    for r in rows:
        if r["last_watched_on"] >= cutoff:
            continue  # still watching
        total = r["total_episodes"]
        if total and r["episodes_watched"] >= total:
            continue  # finished
        new_season = bool(r["total_seasons"] and r["last_season_watched"]
                          and r["total_seasons"] > r["last_season_watched"])
        new_since = bool(r["last_air_date"] and r["last_air_date"] > r["last_watched_on"])
        out.append({
            "title_id": r["id"], "title": r["name"], "profile": r["profile"],
            "episodes_watched": r["episodes_watched"], "total_episodes": total,
            "last_season_watched": r["last_season_watched"], "total_seasons": r["total_seasons"],
            "last_watched_on": r["last_watched_on"], "series_status": r["series_status"],
            "new_season_available": new_season,
            "new_episodes_since_you_stopped": new_since,
            "still_on_netflix": r["netflix_status"] != "removed",
        })
    # Shows with a new season first, then the most recently dropped.
    out.sort(key=lambda d: d["last_watched_on"], reverse=True)
    out.sort(key=lambda d: not d["new_season_available"])
    return out


def shared(conn: sqlite3.Connection) -> dict:
    """What profiles on the account have in common, and what to watch together."""
    rows = conn.execute(
        """SELECT p.name AS profile, t.id, t.name, t.genres, t.netflix_status
           FROM viewings v JOIN titles t ON t.id = v.title_id JOIN profiles p ON p.id = v.profile_id
           GROUP BY p.id, t.id""").fetchall()
    seen: dict[str, set[int]] = defaultdict(set)
    taste: dict[str, Counter] = defaultdict(Counter)
    info: dict[int, sqlite3.Row] = {}
    for r in rows:
        seen[r["profile"]].add(r["id"])
        info[r["id"]] = r
        for g in json.loads(r["genres"] or "[]"):
            taste[r["profile"]][g] += 1
    names = sorted(seen)
    overlap = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            common = seen[a] & seen[b]
            union = seen[a] | seen[b]
            overlap.append({"profiles": [a, b], "titles_in_common": len(common),
                            "similarity": round(len(common) / len(union), 2) if union else 0,
                            "examples": sorted(info[t]["name"] for t in common)[:10]})

    # Watch-together suggestions: titles one profile watched that fit everyone else's taste.
    suggestions = []
    if len(names) > 1:
        def norm(c: Counter) -> dict:
            total = sum(c.values()) or 1
            return {g: n / total for g, n in c.items()}
        tastes = {p: norm(taste[p]) for p in names}
        for tid, r in info.items():
            if r["netflix_status"] == "removed":
                continue
            watchers = [p for p in names if tid in seen[p]]
            others = [p for p in names if tid not in seen[p]]
            if not others:
                continue
            genres = json.loads(r["genres"] or "[]")
            score = sum(tastes[p].get(g, 0) for p in others for g in genres) / len(others)
            if score > 0:
                suggestions.append({"title": r["name"], "watched_by": watchers, "not_yet": others,
                                    "score": round(score, 3)})
        suggestions.sort(key=lambda s: (-len(s["not_yet"]), -s["score"]))
    return {"profiles": names, "overlap": overlap, "watch_together": suggestions[:15]}
