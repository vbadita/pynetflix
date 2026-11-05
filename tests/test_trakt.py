import json

import pytest
from conftest import FIXTURES, FakeTMDB

from pynetflix import exporters
from pynetflix.enrich import enrich_titles
from pynetflix.exporters import Trakt
from pynetflix.importers import import_data_export


class FakeResponse:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body if body is not None else {}

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    """Records requests and replays queued responses (a default response when the queue is empty)."""

    def __init__(self, responses=None, default=None):
        self.headers = {}
        self.calls = []
        self.responses = list(responses or [])
        self.default = default or FakeResponse()

    def post(self, url, json=None, **kw):
        self.calls.append((url, json))
        return self.responses.pop(0) if self.responses else self.default


def make_trakt(tmp_path, session, token=True):
    t = Trakt("cid", "secret", tmp_path / "trakt_token.json")
    t.session = session
    if token:
        t.token_path.write_text(json.dumps({"access_token": "tok"}))
    return t


def enriched(conn):
    import_data_export(conn, FIXTURES / "netflix-report.zip")
    enrich_titles(conn, FakeTMDB(), "RO")


def test_push_requires_login(conn, tmp_path):
    t = make_trakt(tmp_path, FakeSession(), token=False)
    with pytest.raises(RuntimeError, match="trakt login"):
        t.push_history(conn)


def test_push_history_payload(conn, tmp_path):
    enriched(conn)
    session = FakeSession(default=FakeResponse(body={"added": {"movies": 1, "episodes": 3}}))
    result = make_trakt(tmp_path, session).push_history(conn)

    assert session.headers["Authorization"] == "Bearer tok"
    assert len(session.calls) == 1
    url, payload = session.calls[0]
    assert url == f"{exporters.TRAKT_API}/sync/history"
    assert all(m["ids"]["tmdb"] and m["watched_at"] for m in payload["movies"])
    for show in payload["shows"]:
        for season in show["seasons"]:
            assert season["number"] and all(e["number"] for e in season["episodes"])
    assert payload["movies"] or payload["shows"]
    assert result["movies"] == 1 and result["episodes"] == 3


def test_push_history_batches(conn, tmp_path):
    enriched(conn)
    session = FakeSession(default=FakeResponse(body={"added": {"movies": 1, "episodes": 1}}))
    result = make_trakt(tmp_path, session).push_history(conn, batch=1)
    assert len(session.calls) > 1
    assert all(len(p["movies"]) <= 1 and len(p["shows"]) <= 1 for _, p in session.calls)
    assert result["movies"] == len(session.calls)


def test_push_history_since_filters_everything(conn, tmp_path):
    enriched(conn)
    session = FakeSession()
    result = make_trakt(tmp_path, session).push_history(conn, since="2999-01-01")
    assert session.calls == []
    assert result["movies"] == 0 and result["episodes"] == 0


def test_push_history_http_error(conn, tmp_path):
    enriched(conn)
    session = FakeSession(default=FakeResponse(status_code=500))
    with pytest.raises(RuntimeError):
        make_trakt(tmp_path, session).push_history(conn)


def test_login_device_flow(tmp_path, monkeypatch):
    monkeypatch.setattr(exporters.time, "sleep", lambda s: None)
    session = FakeSession(responses=[
        FakeResponse(body={"verification_url": "https://trakt.tv/activate", "user_code": "ABCD",
                           "device_code": "dev", "expires_in": 600, "interval": 1}),
        FakeResponse(status_code=400),  # still pending
        FakeResponse(body={"access_token": "newtok"}),
    ])
    t = make_trakt(tmp_path, session, token=False)
    messages = []
    t.login(prompt=messages.append)
    assert "ABCD" in messages[0]
    assert json.loads(t.token_path.read_text())["access_token"] == "newtok"
    assert oct(t.token_path.stat().st_mode & 0o777) == "0o600"


def test_login_times_out(tmp_path, monkeypatch):
    monkeypatch.setattr(exporters.time, "sleep", lambda s: None)
    session = FakeSession(responses=[FakeResponse(body={
        "verification_url": "u", "user_code": "X", "device_code": "d", "expires_in": -1, "interval": 1})])
    with pytest.raises(TimeoutError):
        make_trakt(tmp_path, session, token=False).login(prompt=lambda m: None)
