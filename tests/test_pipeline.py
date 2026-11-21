import time
from datetime import date, timedelta, timezone

import pytest
from conftest import FIXTURES, FakeTMDB

from pynetflix import stats
from pynetflix.availability import (
    check_availability,
    record_availability,
    record_leaving_notice,
    removed_titles,
)
from pynetflix.enrich import enrich_titles
from pynetflix.importers import import_data_export, import_viewing_activity_csv


def test_csv_import_is_idempotent(conn):
    r = import_viewing_activity_csv(conn, FIXTURES / "NetflixViewingHistory.csv", "Valentin")
    assert r.added == 7
    r = import_viewing_activity_csv(conn, FIXTURES / "NetflixViewingHistory.csv", "Valentin")
    assert r.added == 0 and r.merged == 7


def test_export_merges_with_csv(conn):
    import_viewing_activity_csv(conn, FIXTURES / "NetflixViewingHistory.csv", "Valentin")
    r = import_data_export(conn, FIXTURES / "netflix-report.zip")
    # Two sessions of "Secrets" fold into one; the 40s autoplay and the trailer are skipped.
    assert r.profiles == {"Valentin", "Kids"}
    assert r.skipped == 2
    secrets = [h for h in stats.history(conn, profile="Valentin") if h["episode_title"] == "Secrets"]
    assert len(secrets) == 1
    assert secrets[0]["duration_seconds"] == 51 * 60
    assert secrets[0]["episode_number"] == 1
    assert secrets[0]["watched_at"] == "2024-03-15T20:01:00Z"


def test_enrich_and_removal_tracking(conn):
    import_viewing_activity_csv(conn, FIXTURES / "NetflixViewingHistory.csv", "Valentin")
    tmdb = FakeTMDB(available={"dark", "stranger things", "our planet", "mission impossible"})
    r = enrich_titles(conn, tmdb, "RO")
    assert r.enriched == 5 and r.not_found == 0

    # Episode numbers were filled from TMDB season data.
    dark = [h for h in stats.history(conn, search="Dark")]
    assert {h["episode_number"] for h in dark} == {1, 2, 3}

    # The Irishman wasn't on Netflix at the first check: removed, last seen when we watched it.
    removed = removed_titles(conn)
    assert [t["name"] for t in removed] == ["The Irishman"]
    assert removed[0]["netflix_last_seen_available"] == "2024-02-01"
    assert removed[0]["removed_from_netflix"] == date.today().isoformat()
    assert removed[0]["removed_precision"].startswith("between 2024-02-01 and ")

    # Dark leaves later; a leaving notice in the past gives an exact date.
    title_id = conn.execute("SELECT id FROM titles WHERE name = 'Dark'").fetchone()[0]
    conn.execute("UPDATE titles SET netflix_last_seen_available = '2024-11-20' WHERE id = ?", (title_id,))
    record_leaving_notice(conn, title_id, "2024-12-01")
    tmdb.available.discard("dark")
    a = check_availability(conn, tmdb, "RO")
    assert a.newly_removed == 1
    dark_title = stats.title(conn, title_id)
    assert dark_title["removed_from_netflix"] == "2024-12-01"
    assert dark_title["removed_precision"] == "exact"
    assert dark_title["availability_log"][-1]["available"] == 0  # one log row per day

    # Back on Netflix clears the removal.
    tmdb.available.add("dark")
    a = check_availability(conn, tmdb, "RO")
    assert a.back_on_netflix == 1
    assert stats.title(conn, title_id)["netflix_status"] == "available"


def test_wrapped_abandoned_shared(conn):
    import_data_export(conn, FIXTURES / "netflix-report.zip")
    enrich_titles(conn, FakeTMDB(), "RO")

    w = stats.wrapped(conn, profile="Valentin", year=2024)
    assert w["total_hours"] == round((51 + 44 + 209) / 60, 1)
    assert w["top_titles"][0]["title"] == "The Irishman"
    assert w["biggest_binge"] == {"date": "2024-03-15", "title": "Dark", "episodes": 2}
    assert not w["duration_is_estimated"]

    abandoned = stats.abandoned(conn, profile="Valentin")
    assert abandoned[0]["title"] == "Dark"
    assert abandoned[0]["new_season_available"] is True  # watched S1, TMDB says 3 seasons

    s = stats.shared(conn)
    assert s["overlap"][0]["titles_in_common"] == 1
    assert s["overlap"][0]["examples"] == ["The Irishman"]


def test_movie_with_colon_reclassified_as_series(conn):
    # "Show: Episode" with no season marker first lands as a movie.
    path = FIXTURES.parent / "_tmp.csv"
    path.write_text('Title,Date\n"Our Planet: One Planet","6/1/24"\n')
    try:
        import_viewing_activity_csv(conn, path, "Valentin")
        r = enrich_titles(conn, FakeTMDB(), "RO")
        assert r.reclassified == 1
        assert conn.execute("SELECT kind FROM titles").fetchall()[0][0] == "series"
        # Re-importing now goes straight to the series via the alias.
        r2 = import_viewing_activity_csv(conn, path, "Valentin")
        assert r2.merged == 1 and r2.added == 0
        assert conn.execute("SELECT COUNT(*) FROM titles").fetchone()[0] == 1
    finally:
        path.unlink()


def test_ratings_import(conn):
    from pynetflix.importers import import_ratings
    import_data_export(conn, FIXTURES / "netflix-report.zip")
    assert import_ratings(conn, FIXTURES / "netflix-report.zip") == 3  # star-only row skipped
    loved = stats.ratings(conn, rating=2)
    assert [(r["profile"], r["title"]) for r in loved] == [("Valentin", "Dark")]
    irishman = [h for h in stats.history(conn, search="Irishman")]
    assert {h["profile"]: h["rating"] for h in irishman} == {"Valentin": -1, "Kids": 1}


def test_watch_reports_changes(conn):
    from pynetflix.availability import record_leaving_notice, watch
    import_viewing_activity_csv(conn, FIXTURES / "NetflixViewingHistory.csv", "Valentin")
    tmdb = FakeTMDB(available={"dark", "stranger things", "our planet", "mission impossible", "the irishman"})
    enrich_titles(conn, tmdb, "RO")
    assert watch(conn, tmdb, "RO")["newly_left"] == []

    tmdb.available.discard("dark")
    dark_id = conn.execute("SELECT id FROM titles WHERE name = 'Dark'").fetchone()[0]
    stranger_id = conn.execute("SELECT id FROM titles WHERE name = 'Stranger Things'").fetchone()[0]
    record_leaving_notice(conn, stranger_id, "2999-01-01")
    report = watch(conn, tmdb, "RO")
    assert [t["name"] for t in report["newly_left"]] == ["Dark"]
    assert [t["name"] for t in report["leaving_soon"]] == ["Stranger Things"]
    # Already reported once: the next run doesn't list it as new again.
    assert watch(conn, tmdb, "RO")["newly_left"] == []

    tmdb.available.add("dark")
    assert [t["name"] for t in watch(conn, tmdb, "RO")["back_on_netflix"]] == ["Dark"]
    assert dark_id


def test_resolve_country(conn):
    from pynetflix.availability import resolve_country
    assert resolve_country(conn, None) == "US"  # nothing imported yet
    import_data_export(conn, FIXTURES / "netflix-report.zip")
    assert resolve_country(conn, None) == "RO"  # taken from the export's viewings
    assert resolve_country(conn, "de") == "DE"  # explicit setting wins


def test_my_list_import_flags_removed(conn, tmp_path):
    from pynetflix.importers import import_my_list
    csv_path = tmp_path / "MyList.csv"
    csv_path.write_text(
        "Profile Name,Title Name,Country,Utc Title Add Date\n"
        "Valentin,Dark,RO,2024-01-05 10:00:00\n"
        "Valentin,Our Planet,RO,2024-02-06 11:00:00\n"
        ",Nobody,RO,2024-02-06 11:00:00\n", encoding="utf-8")
    assert import_my_list(conn, csv_path) == 2
    assert import_my_list(conn, csv_path) == 2  # idempotent
    assert len(stats.my_list(conn)) == 2
    dark = conn.execute("SELECT id FROM titles WHERE name = 'Dark'").fetchone()["id"]
    record_availability(conn, dark, "RO", False)
    removed = stats.my_list(conn, removed_only=True)
    assert [(r["title"], r["netflix_status"]) for r in removed] == [("Dark", "removed")]
    assert stats.my_list(conn)[0]["title"] == "Dark"  # removed first
    assert stats.my_list(conn, profile="Kids") == []
    assert import_my_list(conn, tmp_path / "nothing.csv") == 0


def test_export_skips_malformed_rows(conn, tmp_path):
    f = tmp_path / "ViewingActivity.csv"
    f.write_text(
        "Profile Name,Start Time,Duration,Attributes,Title,Supplemental Video Type,Device Type,"
        "Bookmark,Latest Bookmark,Country\n"
        "Ann,2024-01-02 20:00:00,01:30:00,,The Irishman,,TV,,,RO (Romania)\n"
        "Ann,,01:30:00,,Dark: Season 1: Secrets,,TV,,,RO (Romania)\n"
        "Ann,not a date,01:30:00,,Dark: Season 1: Lies,,TV,,,RO (Romania)\n"
        ",2024-01-03 20:00:00,01:30:00,,Our Planet,,TV,,,RO (Romania)\n"
    )
    result = import_data_export(conn, f)
    assert result.added == 1
    assert result.skipped == 3


def test_leaving_soon_uses_local_date(conn, monkeypatch):
    from pynetflix import db
    from pynetflix.availability import leaving_soon

    import_viewing_activity_csv(conn, FIXTURES / "NetflixViewingHistory.csv", "Valentin")
    title_id = conn.execute("SELECT id FROM titles WHERE normalized_name = 'dark'").fetchone()[0]
    record_leaving_notice(conn, title_id, "2024-06-30")
    # The last day counts as "leaving soon" until it is over in local time, whatever UTC says.
    monkeypatch.setattr(db, "today", lambda: "2024-06-30")
    assert [t["name"] for t in leaving_soon(conn)] == ["Dark"]
    assert [t["name"] for t in leaving_soon(conn, today="2024-06-29")] == ["Dark"]
    assert leaving_soon(conn, today="2024-07-01") == []


def test_check_availability_survives_tmdb_errors(conn):
    import requests

    from pynetflix.availability import watch

    class FlakyTMDB(FakeTMDB):
        broken: set[int] = set()

        def watch_providers(self, tmdb_id, kind):
            if tmdb_id in self.broken:
                raise requests.HTTPError("404 Client Error: Not Found")
            return super().watch_providers(tmdb_id, kind)

    import_viewing_activity_csv(conn, FIXTURES / "NetflixViewingHistory.csv", "Valentin")
    tmdb = FlakyTMDB()
    enrich_titles(conn, tmdb, "RO")
    total = conn.execute("SELECT COUNT(*) FROM titles WHERE tmdb_id IS NOT NULL").fetchone()[0]

    # Dark's TMDB lookup fails and Stranger Things has left: the run still checks everything
    # else, and Dark keeps its previous status instead of being marked removed.
    tmdb.broken = {FakeTMDB.CATALOG[("series", "dark")]["id"]}
    tmdb.available.discard("stranger things")
    r = check_availability(conn, tmdb, "RO")
    assert (r.checked, r.failed, r.newly_removed) == (total - 1, 1, 1)
    status = dict(conn.execute("SELECT name, netflix_status FROM titles").fetchall())
    assert status["Dark"] == "available" and status["Stranger Things"] == "removed"

    report = watch(conn, tmdb, "RO")
    assert report["failed"] == 1 and report["checked"] == total - 1


def test_enrich_survives_tmdb_errors(conn):
    import requests

    class FlakyTMDB(FakeTMDB):
        broken: set[str] = set()

        def search(self, name, kind, year=None):
            if name in self.broken:
                raise requests.Timeout("Read timed out")
            return super().search(name, kind, year)

    import_viewing_activity_csv(conn, FIXTURES / "NetflixViewingHistory.csv", "Valentin")
    tmdb = FlakyTMDB()
    tmdb.broken = {"Dark"}
    r = enrich_titles(conn, tmdb, "RO")
    assert r.failed == 1 and r.enriched > 0
    enriched = dict(conn.execute("SELECT name, enriched_at IS NOT NULL FROM titles").fetchall())
    assert enriched["Dark"] == 0 and enriched["Stranger Things"] == 1

    # Next run (TMDB is back): only Dark is left to do, and it gets enriched.
    tmdb.broken = set()
    r = enrich_titles(conn, tmdb, "RO")
    assert (r.enriched, r.failed) == (1, 0)
    dark = conn.execute("SELECT tmdb_id, netflix_status FROM titles WHERE name = 'Dark'").fetchone()
    assert tuple(dark) == (FakeTMDB.CATALOG[("series", "dark")]["id"], "available")


def test_local_hour_converts_utc_watch_times():
    bucharest_winter = timezone(timedelta(hours=2))
    assert stats._local_hour("2024-01-10T21:30:00Z", bucharest_winter) == 23
    assert stats._local_hour("2024-01-10T22:15:00", bucharest_winter) == 0
    assert stats._local_hour("2024-01-10T21:30:00Z", timezone.utc) == 21
    assert stats._local_hour("2024-01-10T21:30:00+00:00", timezone(timedelta(hours=-5))) == 16


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="needs time.tzset")
def test_wrapped_night_owl_share_uses_local_time(conn, monkeypatch, tmp_path):
    # 21:30 and 22:00 UTC are 23:30 and 00:00 in Bucharest (UTC+2 in January).
    path = tmp_path / "ViewingActivity.csv"
    path.write_text(
        "Profile Name,Start Time,Duration,Attributes,Title,Supplemental Video Type,Device Type,"
        "Bookmark,Latest Bookmark,Country\n"
        "Valentin,2024-01-10 21:30:00,00:40:00,,The Irishman,,TV,,,RO (Romania)\n"
        "Valentin,2024-01-11 22:00:00,00:50:00,,Mission: Impossible,,TV,,,RO (Romania)\n"
        "Valentin,2024-01-12 12:00:00,00:30:00,,Dark: Season 1: Secrets,,TV,,,RO (Romania)\n"
    )
    import_data_export(conn, path)

    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    assert stats.wrapped(conn, year=2024)["night_owl_share"] == 0

    monkeypatch.setenv("TZ", "Europe/Bucharest")
    time.tzset()
    try:
        assert stats.wrapped(conn, year=2024)["night_owl_share"] == round(2 / 3, 2)
    finally:
        monkeypatch.undo()
        time.tzset()


def test_enrich_fills_episode_numbers_for_later_imports(conn, tmp_path):
    first = tmp_path / "first.csv"
    first.write_text('Title,Date\n"Dark: Season 1: Secrets","3/15/24"\n', encoding="utf-8")
    import_viewing_activity_csv(conn, first, "Valentin")
    tmdb = FakeTMDB()
    assert enrich_titles(conn, tmdb, "RO").episodes_filled == 1

    # New episodes of an already enriched show arrive later (CSV import or sync).
    later = tmp_path / "later.csv"
    later.write_text('Title,Date\n"Dark: Season 1: Lies","3/16/24"\n'
                     '"Dark: Season 1: Past and Present","3/17/24"\n', encoding="utf-8")
    import_viewing_activity_csv(conn, later, "Valentin")
    r = enrich_titles(conn, tmdb, "RO")
    assert r.enriched == 0 and r.episodes_filled == 2
    assert sorted(h["episode_number"] for h in stats.history(conn, search="Dark")) == [1, 2, 3]

    # Nothing left to fill: the next run doesn't look up any seasons.
    calls = []
    tmdb.season = lambda tmdb_id, n: calls.append((tmdb_id, n)) or {"episodes": []}
    assert enrich_titles(conn, tmdb, "RO").episodes_filled == 0
    assert calls == []


def test_abandoned_counts_rewatched_episodes_once(conn, tmp_path):
    # Dark S1 has 4 episodes; 3 watched, one of them again on a later day.
    import_viewing_activity_csv(conn, FIXTURES / "NetflixViewingHistory.csv", "Valentin")
    rewatch = tmp_path / "rewatch.csv"
    rewatch.write_text('Title,Date\n"Dark: Season 1: Secrets","3/20/24"\n')
    import_viewing_activity_csv(conn, rewatch, "Valentin")
    conn.execute("UPDATE titles SET total_episodes = 4 WHERE name = 'Dark'")

    dark = [a for a in stats.abandoned(conn, profile="Valentin") if a["title"] == "Dark"]
    assert len(dark) == 1  # not mistaken for finished
    assert dark[0]["episodes_watched"] == 3


def test_export_dates_viewings_by_local_day(conn, tmp_path):
    # 22:30 UTC on 15 March is 00:30 on 16 March in Bucharest (UTC+2), where the CSV lists it.
    bucharest = timezone(timedelta(hours=2))
    csv_path = tmp_path / "NetflixViewingHistory.csv"
    csv_path.write_text('Title,Date\n"Dark: Season 1: Lies","3/16/24"\n', encoding="utf-8")
    export = tmp_path / "ViewingActivity.csv"
    export.write_text(
        "Profile Name,Start Time,Duration,Attributes,Title,Supplemental Video Type,Device Type,"
        "Bookmark,Latest Bookmark,Country\n"
        "Valentin,2024-03-15 22:30:00,00:20:00,,Dark: Season 1: Lies,,TV,,,RO (Romania)\n"
        "Valentin,2024-03-15 21:50:00,00:24:00,,Dark: Season 1: Lies,,TV,,,RO (Romania)\n"
    )
    import_viewing_activity_csv(conn, csv_path, "Valentin")
    r = import_data_export(conn, export, tz=bucharest)
    # 21:50 UTC is 23:50 local on the 15th: a separate day, so it stays a separate viewing.
    assert (r.added, r.merged) == (1, 1)
    rows = {h["watched_on"]: h for h in stats.history(conn)}
    assert rows["2024-03-16"]["watched_at"] == "2024-03-15T22:30:00Z"
    assert rows["2024-03-16"]["duration_seconds"] == 20 * 60
    assert rows["2024-03-15"]["duration_seconds"] == 24 * 60


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="needs time.tzset")
def test_connect_moves_old_utc_dates_to_local_days(tmp_path, monkeypatch):
    from pynetflix import db

    path = tmp_path / "old.db"
    old = db.connect(path)
    csv_path = tmp_path / "NetflixViewingHistory.csv"
    csv_path.write_text('Title,Date\n"Dark: Season 1: Lies","3/16/24"\n', encoding="utf-8")
    import_viewing_activity_csv(old, csv_path, "Valentin")
    export = tmp_path / "ViewingActivity.csv"
    export.write_text(
        "Profile Name,Start Time,Duration,Attributes,Title,Supplemental Video Type,Device Type,"
        "Bookmark,Latest Bookmark,Country\n"
        "Valentin,2024-03-15 22:30:00,00:20:00,,Dark: Season 1: Lies,,TV,,,RO (Romania)\n"
        "Valentin,2024-03-16 22:40:00,00:50:00,,The Irishman,,TV,,,RO (Romania)\n"
        "Valentin,2024-03-17 22:40:00,00:50:00,,The Irishman,,TV,,,RO (Romania)\n"
    )
    import_data_export(old, export)  # TZ is UTC: stored on the UTC days, like older versions
    with old:
        old.execute("PRAGMA user_version = 0")
    old.close()

    monkeypatch.setenv("TZ", "Europe/Bucharest")
    time.tzset()
    conn = db.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    rows = [(h["title"], h["watched_on"], h["duration_seconds"]) for h in stats.history(conn)]
    # Lies merges into the CSV's row; each Irishman viewing moves a day without merging.
    assert sorted(rows) == [("Dark", "2024-03-16", 20 * 60),
                            ("The Irishman", "2024-03-17", 50 * 60),
                            ("The Irishman", "2024-03-18", 50 * 60)]
    # Re-importing the export now lines up with the migrated rows instead of duplicating them.
    r = import_data_export(conn, export)
    assert (r.added, r.merged) == (0, 3)


def test_wrapped_keeps_movie_and_series_with_same_name_apart(conn, tmp_path):
    # "Death Note" is both a movie and a series on Netflix: two titles, not one.
    path = tmp_path / "ViewingActivity.csv"
    path.write_text(
        "Profile Name,Start Time,Duration,Attributes,Title,Supplemental Video Type,Device Type,"
        "Bookmark,Latest Bookmark,Country\n"
        "Valentin,2024-02-01 20:00:00,01:40:00,,Death Note,,TV,,,RO (Romania)\n"
        "Valentin,2024-02-02 20:00:00,00:25:00,,Death Note: Season 1: Rebirth,,TV,,,RO (Romania)\n"
        "Valentin,2024-02-03 20:00:00,00:25:00,,Death Note: Season 1: Confrontation,,TV,,,RO (Romania)\n"
    )
    import_data_export(conn, path)

    w = stats.wrapped(conn, year=2024)
    assert (w["titles"], w["movies"], w["series"]) == (2, 1, 1)
    assert sorted((t["title"], t["kind"], t["hours"]) for t in w["top_titles"]) == [
        ("Death Note", "movie", 1.7), ("Death Note", "series", 0.8)]
