import asyncio
import csv
import io
import json

from conftest import FIXTURES, FakeTMDB
from fastapi.testclient import TestClient

from pynetflix import cli, db
from pynetflix.api import create_app
from pynetflix.enrich import enrich_titles
from pynetflix.exporters import export_letterboxd
from pynetflix.importers import import_data_export
from pynetflix.report import render_wrapped
from pynetflix.stats import wrapped
from pynetflix.sync import item_to_parsed


def _seeded(tmp_path):
    path = tmp_path / "api.db"
    conn = db.connect(path)
    import_data_export(conn, FIXTURES / "netflix-report.zip")
    enrich_titles(conn, FakeTMDB(available={"dark", "our planet"}), "RO")
    return path, conn


def test_rest_api(tmp_path):
    path, conn = _seeded(tmp_path)
    client = TestClient(create_app(str(path)))
    assert {p["name"] for p in client.get("/profiles").json()} == {"Valentin", "Kids"}
    hist = client.get("/profiles/Valentin/history").json()
    assert hist[0]["title"] == "Dark" and "removed_from_netflix" in hist[0]
    removed = client.get("/titles/removed").json()
    assert [t["name"] for t in removed] == ["The Irishman"]
    assert client.get("/wrapped", params={"year": 2024}).json()["titles"] == 3
    assert "Wrapped" in client.get("/wrapped.html", params={"year": 2024}).text
    month_page = client.get("/wrapped.html", params={"year": 2024, "month": 3, "profile": "Valentin"}).text
    assert "year=2024&amp;month=2&amp;profile=Valentin" in month_page
    assert "month=4" in month_page and "2024 in full" in month_page
    jan = client.get("/wrapped.html", params={"year": 2024, "month": 1}).text
    assert "year=2023&amp;month=12" in jan
    year_page = client.get("/wrapped.html", params={"year": 2024}).text
    assert "Previous year" in year_page and "year=2023" in year_page
    assert client.get("/titles/9999").status_code == 404
    st = client.get("/status").json()
    assert st["profiles"] == 2 and st["removed_from_netflix"] == 1 and st["not_enriched"] == 0


def test_cli_export_has_removed_column(tmp_path, monkeypatch):
    path, _ = _seeded(tmp_path)
    monkeypatch.setenv("PYNETFLIX_HOME", str(tmp_path))
    out = tmp_path / "history.csv"
    cli.main(["--db", str(path), "export", str(out)])
    rows = list(csv.DictReader(out.open()))
    irishman = [r for r in rows if r["title"] == "The Irishman"]
    assert irishman and all(r["removed_from_netflix"] for r in irishman)
    assert all(not r["removed_from_netflix"] for r in rows if r["title"] == "Dark")


def test_cli_export_json_and_api_csv(tmp_path):
    path, _ = _seeded(tmp_path)
    out = tmp_path / "history.json"
    cli.main(["--db", str(path), "export", str(out)])
    rows = json.loads(out.read_text())
    assert any(r["title"] == "The Irishman" and r["removed_from_netflix"] for r in rows)
    forced = tmp_path / "h.txt"
    cli.main(["--db", str(path), "export", str(forced), "--format", "json"])
    assert len(json.loads(forced.read_text())) == len(rows)

    resp = TestClient(create_app(str(path))).get("/export.csv", params={"profile": "Valentin"})
    assert resp.headers["content-type"].startswith("text/csv")
    csv_rows = list(csv.DictReader(io.StringIO(resp.text)))
    assert csv_rows and {r["profile"] for r in csv_rows} == {"Valentin"}
    assert "removed_from_netflix" in csv_rows[0]


def test_letterboxd_and_report(tmp_path):
    _, conn = _seeded(tmp_path)
    out = tmp_path / "lb.csv"
    assert export_letterboxd(conn, out) == 1
    assert "The Irishman,2019,398978,2024-02-02" in out.read_text()
    html = render_wrapped(wrapped(conn, year=2024))
    assert "<title>Netflix Wrapped</title>" in html and "The Irishman" in html


def test_mcp_server_tools(tmp_path):
    from pynetflix.mcp_server import build_server
    path, _ = _seeded(tmp_path)
    server = build_server(str(path))
    names = {t.name for t in asyncio.run(server.list_tools())}
    assert {"viewing_history", "removed_from_netflix", "wrapped", "abandoned_shows"} <= names


def test_sync_item_mapping():
    episode = {"title": "Dark: Season 1: Secrets", "seriesTitle": "Dark", "series": 80100172,
               "seasonDescriptor": "Season 1", "episodeTitle": "Secrets", "movieID": 80083977,
               "date": 1710532860000, "duration": 3060}
    parsed, nid = item_to_parsed(episode)
    assert (parsed.name, parsed.kind, parsed.season_number, parsed.episode_title, nid) == \
        ("Dark", "series", 1, "Secrets", 80100172)
    parsed, nid = item_to_parsed({"title": "The Irishman", "movieID": 80175798})
    assert (parsed.kind, nid) == ("movie", 80175798)


def test_search_names_cast_and_genres(tmp_path):
    path, conn = _seeded(tmp_path)
    conn.execute("UPDATE titles SET cast_members = ? WHERE normalized_name = 'dark'",
                 (json.dumps(["Louis Hofmann", "Lisa Vicari"]),))
    conn.commit()
    from pynetflix import stats
    assert [t["name"] for t in stats.search(conn, "irish")] == ["The Irishman"]
    assert [t["name"] for t in stats.search(conn, "hofmann")] == ["Dark"]
    assert "Dark" in [t["name"] for t in stats.search(conn, "MYSTERY")]
    assert [t["name"] for t in stats.search(conn, "drama mystery")] == ["Dark"]
    assert stats.search(conn, "100%") == [] and stats.search(conn, "  ") == []
    client = TestClient(create_app(str(path)))
    assert [t["name"] for t in client.get("/search", params={"q": "lisa vicari"}).json()] == ["Dark"]
    assert client.get("/search").status_code == 422
