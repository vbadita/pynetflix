"""Send your history elsewhere: Letterboxd (CSV import) and Trakt (API sync)."""

import csv
import io
import json
import sqlite3
import time
from pathlib import Path

import requests

TRAKT_API = "https://api.trakt.tv"
TRAKT_REDIRECT_URI = "urn:ietf:wg:oauth:2.0:oob"  # what device-flow apps use
TOKEN_REFRESH_MARGIN = 3600  # refresh this many seconds before the access token expires

EXPORT_COLUMNS = ["profile", "watched_on", "watched_at", "title", "kind", "season_number",
                  "episode_number", "episode_title", "duration_seconds", "device", "year",
                  "netflix_status", "removed_from_netflix", "netflix_leaving_on"]


def history_to_csv(rows: list[dict], fileobj) -> None:
    w = csv.DictWriter(fileobj, fieldnames=EXPORT_COLUMNS, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)


def history_csv_text(rows: list[dict]) -> str:
    buf = io.StringIO()
    history_to_csv(rows, buf)
    return buf.getvalue()


def export_letterboxd(conn: sqlite3.Connection, out: Path | str, profile: str | None = None) -> int:
    """Write a CSV in Letterboxd's import format (movies only - Letterboxd has no TV).

    Each day you watched a movie becomes its own diary entry, and every watch after the first
    is marked as a rewatch, so a film seen three times shows up three times in your diary.
    Returns the number of diary entries written."""
    params: list = []
    profile_sql = ""
    if profile:
        profile_sql = "AND p.name = ?"
        params.append(profile)
    rows = conn.execute(
        f"""SELECT DISTINCT t.id, t.name, t.year, t.tmdb_id, v.watched_on
            FROM viewings v JOIN titles t ON t.id = v.title_id JOIN profiles p ON p.id = v.profile_id
            WHERE t.kind = 'movie' {profile_sql}
            ORDER BY v.watched_on, t.id""", params).fetchall()
    seen: set[int] = set()
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Title", "Year", "tmdbID", "WatchedDate", "Rewatch"])
        for r in rows:
            rewatch = r["id"] in seen
            seen.add(r["id"])
            w.writerow([r["name"], r["year"] or "", r["tmdb_id"] or "", r["watched_on"],
                        "true" if rewatch else "false"])
    return len(rows)


class Trakt:
    """Trakt sync using the OAuth device flow (no web server needed)."""

    def __init__(self, client_id: str, client_secret: str, token_path: Path):
        self.client_id = client_id
        self.client_secret = client_secret
        self.token_path = token_path
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "trakt-api-version": "2",
            "trakt-api-key": client_id,
        })

    def login(self, prompt=print) -> None:
        code = self.session.post(f"{TRAKT_API}/oauth/device/code", json={"client_id": self.client_id}).json()
        prompt(f"Go to {code['verification_url']} and enter the code: {code['user_code']}")
        deadline = time.time() + code["expires_in"]
        while time.time() < deadline:
            time.sleep(code["interval"])
            resp = self.session.post(f"{TRAKT_API}/oauth/device/token", json={
                "code": code["device_code"], "client_id": self.client_id,
                "client_secret": self.client_secret})
            if resp.status_code == 200:
                self._save_token(resp.json())
                return
            if resp.status_code not in (400, 429):  # 400 = still pending
                resp.raise_for_status()
        raise TimeoutError("Trakt device code expired before it was approved")

    def _save_token(self, token: dict) -> None:
        self.token_path.write_text(json.dumps(token))
        self.token_path.chmod(0o600)

    def _refresh(self, token: dict) -> dict:
        resp = self.session.post(f"{TRAKT_API}/oauth/token", json={
            "refresh_token": token["refresh_token"], "client_id": self.client_id,
            "client_secret": self.client_secret, "redirect_uri": TRAKT_REDIRECT_URI,
            "grant_type": "refresh_token"})
        if resp.status_code in (400, 401, 403):  # refresh token revoked or expired too
            raise RuntimeError("Trakt session expired. Run: pynetflix trakt login")
        resp.raise_for_status()
        new = resp.json()
        self._save_token(new)
        return new

    def _auth(self) -> None:
        if not self.token_path.exists():
            raise RuntimeError("Not logged in to Trakt. Run: pynetflix trakt login")
        token = json.loads(self.token_path.read_text())
        created, lifetime = token.get("created_at"), token.get("expires_in")
        if (created is not None and lifetime is not None and token.get("refresh_token")
                and time.time() >= created + lifetime - TOKEN_REFRESH_MARGIN):
            token = self._refresh(token)
        self.session.headers["Authorization"] = f"Bearer {token['access_token']}"

    def push_history(self, conn: sqlite3.Connection, profile: str | None = None,
                     since: str | None = None, batch: int = 200) -> dict:
        """Add watched movies and episodes to Trakt history. Needs TMDB-enriched titles."""
        self._auth()
        where, params = ["t.tmdb_id IS NOT NULL"], []
        if profile:
            where.append("p.name = ?"); params.append(profile)
        if since:
            where.append("v.watched_on >= ?"); params.append(since)
        rows = conn.execute(
            f"""SELECT t.kind, t.tmdb_id, v.watched_on, v.watched_at, v.season_number, v.episode_number
                FROM viewings v JOIN titles t ON t.id = v.title_id JOIN profiles p ON p.id = v.profile_id
                WHERE {' AND '.join(where)} ORDER BY v.watched_on""", params).fetchall()

        movies, shows, skipped = [], {}, 0
        for r in rows:
            watched_at = r["watched_at"] or f"{r['watched_on']}T20:00:00Z"
            if r["kind"] == "movie":
                movies.append({"ids": {"tmdb": r["tmdb_id"]}, "watched_at": watched_at})
            elif r["season_number"] and r["episode_number"]:
                show = shows.setdefault(r["tmdb_id"], {"ids": {"tmdb": r["tmdb_id"]}, "seasons": {}})
                season = show["seasons"].setdefault(r["season_number"], [])
                season.append({"number": r["episode_number"], "watched_at": watched_at})
            else:
                skipped += 1  # episode number unknown (CSV import); import the data export for these

        show_payload = [{"ids": s["ids"], "seasons": [{"number": n, "episodes": eps}
                                                      for n, eps in s["seasons"].items()]}
                        for s in shows.values()]
        added = {"movies": 0, "episodes": 0}
        for i in range(0, max(len(movies), len(show_payload)), batch):
            resp = self.session.post(f"{TRAKT_API}/sync/history", json={
                "movies": movies[i:i + batch], "shows": show_payload[i:i + batch]})
            resp.raise_for_status()
            body = resp.json().get("added", {})
            added["movies"] += body.get("movies", 0)
            added["episodes"] += body.get("episodes", 0)
        return {**added, "skipped_without_episode_number": skipped}
