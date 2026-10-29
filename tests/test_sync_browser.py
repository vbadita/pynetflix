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

ACTIVITY_HTML = """<html><body><ul id="list"></ul>
<button id="more" style="display:none">Show More</button>
<script>
let page = 0;
async function load() {
  const r = await fetch('/api/shakti/test/viewingactivity?pg=' + page);
  const body = await r.json();
  document.getElementById('more').style.display = body.hasMore ? 'block' : 'none';
  page++;
}
document.getElementById('more').onclick = load;
load();
</script></body></html>"""

ITEMS = {
    "AAAABBBBCCCCDDDD1111": [
        [{"title": "Dark: Season 1: Secrets", "seriesTitle": "Dark", "series": 1, "seasonDescriptor": "Season 1",
          "episodeTitle": "Secrets", "date": 1710532860000, "duration": 3060}],
        [{"title": "The Irishman", "movieID": 2, "date": 1706817600000, "duration": 12540}],
    ],
    "EEEEFFFFGGGGHHHH2222": [[{"title": "The Irishman", "movieID": 2, "date": 1706904000000, "duration": 100}]],
}


def test_sync_against_mock(tmp_path, monkeypatch):

    session = tmp_path / "session.json"
    session.write_text(json.dumps({"cookies": [], "origins": []}))
    state = {"guid": None}

    def handler(route):
        url = route.request.url
        if url.endswith("/account/profiles"):
            return route.fulfill(body=PROFILES_HTML, content_type="text/html")
        if "/settings/viewed/" in url:
            state["guid"] = url.rsplit("/", 1)[1]
            return route.fulfill(body=ACTIVITY_HTML, content_type="text/html")
        if "viewingactivity" in url:
            pages = ITEMS[state["guid"]]
            pg = int(url.split("pg=")[1])
            body = {"viewedItems": pages[pg], "hasMore": pg + 1 < len(pages)}
            return route.fulfill(body=json.dumps(body), content_type="application/json")
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
    assert hist[0]["watched_at"] == "2024-03-15T20:01:00Z"
