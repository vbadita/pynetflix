from pathlib import Path

import pytest

from pynetflix import db

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def conn(tmp_path):
    return db.connect(tmp_path / "test.db")


class FakeTMDB:
    """Stands in for the TMDB API. `on_netflix` controls availability per title name."""

    CATALOG = {
        ("movie", "the irishman"): {"id": 398978, "release_date": "2019-11-01", "runtime": 209,
                                    "genres": [{"name": "Crime"}, {"name": "Drama"}]},
        ("movie", "mission impossible"): {"id": 954, "release_date": "1996-05-22", "runtime": 110,
                                          "genres": [{"name": "Action"}]},
        ("series", "dark"): {"id": 70523, "first_air_date": "2017-12-01", "episode_run_time": [55],
                             "number_of_seasons": 3, "number_of_episodes": 26, "last_air_date": "2020-06-27",
                             "status": "Ended", "genres": [{"name": "Drama"}, {"name": "Mystery"}]},
        ("series", "stranger things"): {"id": 66732, "first_air_date": "2016-07-15", "episode_run_time": [50],
                                        "number_of_seasons": 5, "number_of_episodes": 42,
                                        "last_air_date": "2025-12-31", "status": "Ended",
                                        "genres": [{"name": "Drama"}, {"name": "Sci-Fi & Fantasy"}]},
        ("series", "our planet"): {"id": 85948, "first_air_date": "2019-04-05", "episode_run_time": [50],
                                   "number_of_seasons": 1, "number_of_episodes": 8,
                                   "genres": [{"name": "Documentary"}]},
    }
    SEASONS = {(70523, 1): [{"episode_number": 1, "name": "Secrets"}, {"episode_number": 2, "name": "Lies"},
                            {"episode_number": 3, "name": "Past and Present"}]}

    def __init__(self, available: set[str] | None = None):
        self.available = available if available is not None else {n for _, n in self.CATALOG}

    def _providers(self, tmdb_id):
        name = next(n for (_, n), d in self.CATALOG.items() if d["id"] == tmdb_id)
        flatrate = [{"provider_id": 8, "provider_name": "Netflix"}] if name in self.available else []
        return {"RO": {"flatrate": flatrate}}

    def search(self, name, kind, year=None):
        from pynetflix.parsing import normalize
        d = self.CATALOG.get((kind, normalize(name)))
        return {"id": d["id"]} if d else None

    def details(self, tmdb_id, kind):
        d = next(d for d in self.CATALOG.values() if d["id"] == tmdb_id)
        return {**d, "credits": {"cast": [{"name": "Somebody"}]},
                "watch/providers": {"results": self._providers(tmdb_id)}}

    def season(self, tmdb_id, n):
        return {"episodes": self.SEASONS.get((tmdb_id, n), [])}

    def watch_providers(self, tmdb_id, kind):
        return self._providers(tmdb_id)
