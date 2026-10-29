import asyncio
import csv

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
    path, _ = _seeded(tmp_path)
    client = TestClient(create_app(str(path)))
    assert {p["name"] for p in client.get("/profiles").json()} == {"Valentin", "Kids"}
    hist = client.get("/profiles/Valentin/history").json()
    assert hist[0]["title"] == "Dark" and "removed_from_netflix" in hist[0]
    removed = client.get("/titles/removed").json()
    assert [t["name"] for t in removed] == ["The Irishman"]
    assert client.get("/wrapped", params={"year": 2024}).json()["titles"] == 3
    assert "Wrapped" in client.get("/wrapped.html", params={"year": 2024}).text
    assert client.get("/titles/9999").status_code == 404


def test_cli_export_has_removed_column(tmp_path, monkeypatch):
    path, _ = _seeded(tmp_path)
    monkeypatch.setenv("PYNETFLIX_HOME", str(tmp_path))
    out = tmp_path / "history.csv"
    cli.main(["--db", str(path), "export", str(out)])
    rows = list(csv.DictReader(out.open()))
    irishman = [r for r in rows if r["title"] == "The Irishman"]
    assert irishman and all(r["removed_from_netflix"] for r in irishman)
    assert all(not r["removed_from_netflix"] for r in rows if r["title"] == "Dark")


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
