"""Import viewing history from files Netflix gives you.

* Viewing Activity CSV  - netflix.com/viewingactivity -> "Download all" (one profile, dates only)
* Personal data export  - Account -> "Download your personal information" (ZIP, all profiles,
                          timestamps, durations, devices)
"""

import csv
import io
import sqlite3
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import db
from .parsing import parse_duration, parse_title

_CSV_DATE_FORMATS = ("%m/%d/%y", "%d/%m/%y", "%m/%d/%Y", "%d/%m/%Y", "%Y-%m-%d", "%d.%m.%y", "%d.%m.%Y")


@dataclass
class ImportResult:
    added: int = 0
    merged: int = 0
    skipped: int = 0
    profiles: set[str] = field(default_factory=set)

    def __str__(self) -> str:
        return (f"{self.added} new viewings, {self.merged} merged with existing, "
                f"{self.skipped} skipped, profiles: {', '.join(sorted(self.profiles)) or '-'}")


def detect_date_format(values: list[str]) -> str:
    """Pick the first format that parses every value (US m/d/y wins ties, like Netflix US)."""
    for fmt in _CSV_DATE_FORMATS:
        try:
            for v in values:
                datetime.strptime(v, fmt)
            return fmt
        except ValueError:
            continue
    raise ValueError(f"Could not recognise date format in values like {values[:3]!r}; "
                     "pass date_format explicitly")


def import_viewing_activity_csv(conn: sqlite3.Connection, path: Path | str, profile: str,
                                date_format: str | None = None) -> ImportResult:
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = [r for r in csv.DictReader(f) if r.get("Title")]
    result = ImportResult(profiles={profile})
    if not rows:
        return result
    fmt = date_format or detect_date_format([r["Date"] for r in rows])
    profile_id = db.get_or_create_profile(conn, profile)
    with conn:
        for r in rows:
            watched_on = datetime.strptime(r["Date"], fmt).date().isoformat()
            parsed = parse_title(r["Title"])
            title_id = db.get_or_create_title(conn, parsed)
            new = db.upsert_viewing(conn, profile_id=profile_id, title_id=title_id, parsed=parsed,
                                    raw_title=r["Title"], watched_on=watched_on, source="csv")
            if new:
                result.added += 1
            else:
                result.merged += 1
    return result


def _open_export_csv(path: Path, filename: str = "ViewingActivity.csv") -> io.TextIOBase | None:
    if zipfile.is_zipfile(path):
        zf = zipfile.ZipFile(path)
        name = next((n for n in zf.namelist() if n.endswith(filename)), None)
        if not name:
            return None
        return io.TextIOWrapper(zf.open(name), encoding="utf-8-sig", newline="")
    if path.name != filename:
        return None
    return open(path, newline="", encoding="utf-8-sig")


# Netflix's "Thumbs Value": 1 = thumbs down, 2 = thumbs up, 3 = two thumbs up ("love this").
THUMBS = {"1": -1, "2": 1, "3": 2}


def import_ratings(conn: sqlite3.Connection, path: Path | str) -> int:
    """Import thumbs ratings from the export's Ratings.csv. Returns how many were stored."""
    f = _open_export_csv(Path(path), "Ratings.csv")
    if f is None:
        return 0
    stored = 0
    with f, conn:
        for r in csv.DictReader(f):
            rating = THUMBS.get((r.get("Thumbs Value") or "").strip())
            name = (r.get("Title Name") or "").strip()
            if rating is None or not name:
                continue
            parsed = parse_title(name)
            title_id = db.get_or_create_title(conn, parsed)
            profile_id = db.get_or_create_profile(conn, r["Profile Name"])
            rated_at = (r.get("Event Utc Ts") or "").strip() or None
            conn.execute(
                "INSERT INTO ratings (profile_id, title_id, rating, rated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (profile_id, title_id) DO UPDATE SET rating = excluded.rating, "
                "rated_at = excluded.rated_at WHERE excluded.rated_at >= COALESCE(ratings.rated_at, '')",
                (profile_id, title_id, rating, rated_at),
            )
            stored += 1
    return stored


def import_data_export(conn: sqlite3.Connection, path: Path | str,
                       min_seconds: int = 60) -> ImportResult:
    """Import the personal-data export (ZIP or its ViewingActivity.csv)."""
    result = ImportResult()
    # Netflix logs one row per playback session; fold sessions of the same episode on the
    # same day into one viewing so it lines up with the CSV and sync sources.
    grouped: dict[tuple, dict] = {}
    f = _open_export_csv(Path(path))
    if f is None:
        raise ValueError("No ViewingActivity.csv found in the export")
    with f:
        for r in csv.DictReader(f):
            if (r.get("Supplemental Video Type") or "").strip():
                result.skipped += 1  # trailers, hooks, teasers
                continue
            seconds = parse_duration(r.get("Duration")) or 0
            started = datetime.strptime(r["Start Time"].strip(), "%Y-%m-%d %H:%M:%S")
            key = (r["Profile Name"], r["Title"], started.date().isoformat())
            g = grouped.get(key)
            if g is None:
                grouped[key] = {"start": started, "seconds": seconds,
                                "device": r.get("Device Type") or None,
                                "country": (r.get("Country") or "").split(" ")[0] or None}
            else:
                g["seconds"] += seconds
                g["start"] = min(g["start"], started)

    with conn:
        profile_ids: dict[str, int] = {}
        for (profile, raw_title, watched_on), g in grouped.items():
            if g["seconds"] < min_seconds:
                result.skipped += 1  # autoplay previews and accidental clicks
                continue
            if profile not in profile_ids:
                profile_ids[profile] = db.get_or_create_profile(conn, profile)
                result.profiles.add(profile)
            parsed = parse_title(raw_title)
            title_id = db.get_or_create_title(conn, parsed)
            new = db.upsert_viewing(
                conn, profile_id=profile_ids[profile], title_id=title_id, parsed=parsed,
                raw_title=raw_title, watched_on=watched_on, source="export",
                watched_at=g["start"].isoformat() + "Z", duration_seconds=g["seconds"],
                device=g["device"], country=g["country"],
            )
            if new:
                result.added += 1
            else:
                result.merged += 1
    return result
