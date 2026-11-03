"""Drives the real sync code in headless Chromium against a mocked netflix.com."""

import json
import os

import pytest

from pynetflix import db, stats, sync

pytest.importorskip("playwright")

PROFILES_HTML = """<html><body>
<a href="/settings/viewed/AAAABBBBCCCCDDDD1111">Valentin</a>
<a href="/settings/viewed/EEEEFFFFGGGGHHHH2222">Kids</a>
</body></html>"""

def activity_page(pages, lang="ro"):
    """A viewing-activity page like Netflix's: rows in the HTML, 'show more' appends the next batch."""
    def rows(batch):
        return "".join(
            f'<li class="retableRow" data-uia="activity-row"><div class="col date nowrap">{d}</div>'
            f'<div class="col title"><a href="/title/{vid}">{t}</a></div></li>' for d, vid, t in batch)
    first, rest = pages[0], pages[1:]
    return (f'<html lang="{lang}"><body><ul class="retable">{rows(first)}</ul>'
            f'<button class="btn btn-blue btn-small" id="more" style="display:{"block" if rest else "none"}">'
            f'Afișează mai multe</button><script>'
            f'const rest = {json.dumps([rows(b) for b in rest])};'
            f'document.getElementById("more").onclick = () => {{'
            f'document.querySelector("ul").insertAdjacentHTML("beforeend", rest.shift());'
            f'if (!rest.length) {{ const b = document.getElementById("more");'
            f' b.disabled = true; b.className += " disabled"; }} }};'
            f'</script></body></html>')


# (date as shown, netflix id of the watched video, title) in Romanian day.month.year format
PAGES = {
    "AAAABBBBCCCCDDDD1111": [
        [("15.3.24", 70, "Dark: Sezonul 1: Secrets")],
        [("2.2.24", 2, "The Irishman")],
    ],
    "EEEEFFFFGGGGHHHH2222": [[("3.2.24", 2, "The Irishman")]],
}
ACTIVITY_HTML = activity_page(PAGES["EEEEFFFFGGGGHHHH2222"])


PIN_HTML = """<html><body><h1>Profile Lock</h1><input data-uia="pin-input"></body></html>"""


_REAL_PLAYWRIGHT = sync._playwright


def _wrapped(handler):
    real = _REAL_PLAYWRIGHT

    class Wrapped:
        def __enter__(self):
            self.cm = real()
            p = self.cm.__enter__()
            launch = p.chromium.launch

            def patched_launch(**kw):
                if os.environ.get("PW_CHROMIUM"):
                    kw["executable_path"] = os.environ["PW_CHROMIUM"]
                browser = launch(**kw)
                new_context = browser.new_context

                def ctx(**ckw):
                    c = new_context(**ckw)
                    c.route("https://www.netflix.com/**", handler)
                    return c
                browser.new_context = ctx
                return browser
            p.chromium.launch = patched_launch
            return p

        def __exit__(self, *a):
            return self.cm.__exit__(*a)

    return Wrapped()


def test_sync_pin_locked_profile(tmp_path, monkeypatch):
    session = tmp_path / "session.json"
    session.write_text(json.dumps({"cookies": [], "origins": []}))
    visits = {"locked": 0}

    def handler(route):
        url = route.request.url
        if url.endswith("/account/profiles"):
            return route.fulfill(body=PROFILES_HTML, content_type="text/html")
        if "/settings/viewed/EEEE" in url:
            visits["locked"] += 1
            # First visit (headless pass) hits the PIN prompt; the retry is "after typing the PIN".
            body = PIN_HTML if visits["locked"] == 1 else ACTIVITY_HTML
            return route.fulfill(body=body, content_type="text/html")
        if "/settings/viewed/" in url:
            return route.fulfill(body=ACTIVITY_HTML, content_type="text/html")
        return route.fulfill(status=404, body="")

    monkeypatch.setattr(sync, "_playwright", lambda: _wrapped(handler))
    monkeypatch.setattr(sync, "FIRST_ROWS_TIMEOUT_MS", 2_000)
    conn = db.connect(tmp_path / "p.db")
    r = sync.sync(conn, session, pin_headless=True)
    assert r.profiles == ["Valentin (1 items)", "Kids (1 items)"]
    assert visits["locked"] == 2


def test_sync_pin_not_entered_is_skipped(tmp_path, monkeypatch):
    session = tmp_path / "session.json"
    session.write_text(json.dumps({"cookies": [], "origins": []}))

    def handler(route):
        url = route.request.url
        if url.endswith("/account/profiles"):
            return route.fulfill(body=PROFILES_HTML, content_type="text/html")
        if "/settings/viewed/EEEE" in url:
            return route.fulfill(body=PIN_HTML, content_type="text/html")
        if "/settings/viewed/" in url:
            return route.fulfill(body=ACTIVITY_HTML, content_type="text/html")
        return route.fulfill(status=404, body="")

    monkeypatch.setattr(sync, "_playwright", lambda: _wrapped(handler))
    monkeypatch.setattr(sync, "PIN_WAIT_MINUTES", 0.02)
    monkeypatch.setattr(sync, "FIRST_ROWS_TIMEOUT_MS", 2_000)
    conn = db.connect(tmp_path / "p.db")
    r = sync.sync(conn, session, pin_headless=True)
    assert r.profiles[0] == "Valentin (1 items)"
    assert r.profiles[1].startswith("Kids (skipped")


def test_sync_against_mock(tmp_path, monkeypatch):

    session = tmp_path / "session.json"
    session.write_text(json.dumps({"cookies": [], "origins": []}))

    def handler(route):
        url = route.request.url
        if url.endswith("/account/profiles"):
            return route.fulfill(body=PROFILES_HTML, content_type="text/html")
        if "/settings/viewed/" in url:
            return route.fulfill(body=activity_page(PAGES[url.rsplit("/", 1)[1]]), content_type="text/html")
        return route.fulfill(status=404, body="")

    real = sync._playwright

    class Wrapped:
        def __enter__(self):
            self.cm = real()
            p = self.cm.__enter__()
            launch = p.chromium.launch

            def patched_launch(**kw):
                if os.environ.get("PW_CHROMIUM"):  # use a preinstalled browser if given
                    kw["executable_path"] = os.environ["PW_CHROMIUM"]
                browser = launch(**kw)
                new_context = browser.new_context

                def ctx(**ckw):
                    c = new_context(**ckw)
                    c.route("https://www.netflix.com/**", handler)
                    return c
                browser.new_context = ctx
                return browser
            p.chromium.launch = patched_launch
            return p

        def __exit__(self, *a):
            return self.cm.__exit__(*a)

    monkeypatch.setattr(sync, "_playwright", lambda: Wrapped())
    conn = db.connect(tmp_path / "s.db")
    r = sync.sync(conn, session)
    assert r.added == 3
    assert r.profiles == ["Valentin (2 items)", "Kids (1 items)"]
    hist = stats.history(conn, profile="Valentin")
    assert [h["title"] for h in hist] == ["Dark", "The Irishman"]
    assert [h["watched_on"] for h in hist] == ["2024-03-15", "2024-02-02"]
    assert hist[0]["season_number"] == 1


def test_sync_stops_at_disabled_more_button(tmp_path, monkeypatch):
    # After the last batch Netflix keeps the button visible but disabled; sync must not hang on it.
    session = tmp_path / "session.json"
    session.write_text(json.dumps({"cookies": [], "origins": []}))
    pages = [[("1.1.24", 1, "A Movie")], [("2.1.24", 2, "B Movie")], [("3.1.24", 3, "C Movie")]]

    def handler(route):
        if route.request.url.endswith("/account/profiles"):
            return route.fulfill(body='<a href="/settings/viewed/AAAABBBBCCCCDDDD1111">Valentin</a>',
                                 content_type="text/html")
        return route.fulfill(body=activity_page(pages), content_type="text/html")

    monkeypatch.setattr(sync, "_playwright", lambda: _wrapped(handler))
    monkeypatch.setattr(sync, "MORE_ENABLE_WAIT_MS", 500)
    r = sync.sync(db.connect(tmp_path / "d.db"), session)
    assert r.profiles == ["Valentin (3 items)"]


def test_sync_reports_unexpected_page(tmp_path, monkeypatch):
    session = tmp_path / "session.json"
    session.write_text(json.dumps({"cookies": [], "origins": []}))

    def handler(route):
        url = route.request.url
        if url.endswith("/account/profiles"):
            return route.fulfill(body=PROFILES_HTML, content_type="text/html")
        return route.fulfill(body="<html><title>Sign In</title><body>Sign in</body></html>",
                             content_type="text/html")

    monkeypatch.setattr(sync, "_playwright", lambda: _wrapped(handler))
    monkeypatch.setattr(sync, "FIRST_ROWS_TIMEOUT_MS", 1_000)
    with pytest.raises(sync.SyncError, match="Sign In"):
        sync.sync(db.connect(tmp_path / "x.db"), session)


def test_parse_activity_dates():
    assert sync.parse_activity_dates(["21.10.26", "11.1.26"]) == ["2026-10-21", "2026-01-11"]
    assert sync.parse_activity_dates(["3/14/2026", "1/2/2026"]) == ["2026-03-14", "2026-01-02"]  # 14 => month first
    assert sync.parse_activity_dates(["2026-03-14"]) == ["2026-03-14"]
    assert sync.parse_activity_dates(["1/2/2026"], lang="en-US") == ["2026-01-02"]
    assert sync.parse_activity_dates(["1/2/2026"], lang="ro") == ["2026-02-01"]
    with pytest.raises(sync.SyncError):
        sync.parse_activity_dates(["March 14"])
