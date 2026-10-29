from datetime import date

from conftest import FIXTURES, FakeTMDB

from pynetflix import stats
from pynetflix.availability import check_availability, record_leaving_notice, removed_titles
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
