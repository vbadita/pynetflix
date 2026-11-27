"""Minimal TMDB client (https://developer.themoviedb.org). Free API key required."""

import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests

API = "https://api.themoviedb.org/3"
IMAGE_BASE = "https://image.tmdb.org/t/p/w342"
# TMDB's watch-provider data comes from JustWatch. Netflix shows up under a few provider ids.
NETFLIX_PROVIDER_IDS = {8, 175, 1796}  # Netflix, Netflix Kids, Netflix Standard with Ads
MAX_RETRY_WAIT = 60  # never sleep longer than this on a 429, whatever Retry-After says


def retry_after_seconds(value: str | None, default: float) -> float:
    """Seconds to wait from a Retry-After header: delay-seconds ("2", "1.5") or an HTTP date.

    Anything unreadable falls back to ``default``; the result is capped at MAX_RETRY_WAIT."""
    wait = default
    if value:
        try:
            wait = float(value)
        except ValueError:
            try:
                when = parsedate_to_datetime(value)
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
                wait = (when - datetime.now(timezone.utc)).total_seconds()
            except (TypeError, ValueError, IndexError):
                pass
    if wait != wait:  # "nan" parses as a float
        wait = default
    return min(max(wait, 0.0), MAX_RETRY_WAIT)


class TMDB:
    def __init__(self, api_key: str, session: requests.Session | None = None):
        self.session = session or requests.Session()
        # v4 "API Read Access Token" is a JWT; v3 keys are 32 hex chars.
        if api_key.startswith("eyJ"):
            self.session.headers["Authorization"] = f"Bearer {api_key}"
            self.params = {}
        else:
            self.params = {"api_key": api_key}

    def _get(self, path: str, **params) -> dict:
        for attempt in range(4):
            resp = self.session.get(f"{API}{path}", params={**self.params, **params}, timeout=20)
            if resp.status_code == 429:
                # A date or fractional Retry-After used to crash the whole run with ValueError.
                time.sleep(retry_after_seconds(resp.headers.get("Retry-After"), 2 ** attempt))
                continue
            resp.raise_for_status()
            return resp.json()
        resp.raise_for_status()
        return {}

    def search(self, name: str, kind: str, year: int | None = None) -> dict | None:
        endpoint = "/search/movie" if kind == "movie" else "/search/tv"
        params = {"query": name}
        if year:
            params["year" if kind == "movie" else "first_air_date_year"] = year
        results = self._get(endpoint, **params).get("results", [])
        return results[0] if results else None

    def details(self, tmdb_id: int, kind: str) -> dict:
        endpoint = f"/movie/{tmdb_id}" if kind == "movie" else f"/tv/{tmdb_id}"
        return self._get(endpoint, append_to_response="credits,watch/providers")

    def season(self, tmdb_id: int, season_number: int) -> dict:
        return self._get(f"/tv/{tmdb_id}/season/{season_number}")

    def watch_providers(self, tmdb_id: int, kind: str) -> dict:
        endpoint = f"/movie/{tmdb_id}/watch/providers" if kind == "movie" else f"/tv/{tmdb_id}/watch/providers"
        return self._get(endpoint).get("results", {})


def on_netflix(providers_by_country: dict, country: str) -> bool:
    entry = providers_by_country.get(country.upper()) or {}
    for offer in entry.get("flatrate", []) + entry.get("ads", []):
        if offer.get("provider_id") in NETFLIX_PROVIDER_IDS or "netflix" in offer.get("provider_name", "").lower():
            return True
    return False
