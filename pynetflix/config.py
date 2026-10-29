"""Paths and settings. Everything lives under ~/.pynetflix unless overridden."""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Config:
    home: Path
    tmdb_api_key: str | None
    country: str
    trakt_client_id: str | None
    trakt_client_secret: str | None

    @property
    def db_path(self) -> Path:
        return self.home / "pynetflix.db"

    @property
    def session_path(self) -> Path:
        return self.home / "session.json"

    @property
    def trakt_token_path(self) -> Path:
        return self.home / "trakt_token.json"


def load_config() -> Config:
    home = Path(os.environ.get("PYNETFLIX_HOME", Path.home() / ".pynetflix")).expanduser()
    home.mkdir(parents=True, exist_ok=True)
    return Config(
        home=home,
        tmdb_api_key=os.environ.get("TMDB_API_KEY"),
        # ISO 3166-1 country used for Netflix availability checks.
        country=os.environ.get("NETFLIX_COUNTRY", "US").upper(),
        trakt_client_id=os.environ.get("TRAKT_CLIENT_ID"),
        trakt_client_secret=os.environ.get("TRAKT_CLIENT_SECRET"),
    )
