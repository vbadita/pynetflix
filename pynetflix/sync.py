"""Automatic sync from netflix.com with a real browser (Playwright).

You log in yourself in a visible browser window, once. pynetflix never sees your password;
it only saves the session cookies to ~/.pynetflix/session.json (readable by you only).

Netflix has no public API and changes its website without notice, so everything that
depends on its pages lives in this module. If sync breaks, this is the file to fix. The
rest of pynetflix (imports, stats, API) keeps working from the files Netflix lets you export.

Install the extra first:  pip install "pynetflix[sync]" && playwright install chromium
"""

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import db
from .availability import record_leaving_notice
from .parsing import ParsedTitle, parse_title, season_number_from_label

NETFLIX = "https://www.netflix.com"
# Profile GUIDs show up in account-page links such as /settings/viewed/<GUID>.
_GUID_RE = re.compile(r"/(?:settings/[a-z]+|account/profiles?|profiles/manage)/([A-Z0-9]{16,})")
# What Netflix shows instead of the viewing activity when a profile is PIN-locked.
_PIN_SELECTOR = '[data-uia*="pin"], input[name="pin"], input[autocomplete="one-time-code"]'
_PIN_TEXT_RE = re.compile(r"profile lock|enter your pin", re.I)
PIN_WAIT_MINUTES = 5
FIRST_RESPONSE_TIMEOUT_MS = 30_000
_LEAVING_RE = re.compile(r"Last day to watch(?: on Netflix)?:?\s*([A-Z][a-z]+ \d{1,2}(?:, \d{4})?)")


def _playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:  # pragma: no cover
        raise SystemExit('Sync needs Playwright: pip install "pynetflix[sync]" && playwright install chromium') from e
    return sync_playwright()


def login(session_path: Path, timeout_minutes: int = 10) -> None:
    """Open a browser window, let the user log in, and save the session."""
    with _playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(f"{NETFLIX}/login")
        print("Log in to Netflix in the browser window (pick any profile when asked)...")
        page.wait_for_url(re.compile(r"netflix\.com/(browse|profiles|account)"), timeout=timeout_minutes * 60_000)
        context.storage_state(path=str(session_path))
        session_path.chmod(0o600)
        browser.close()
    print(f"Session saved to {session_path}")


class PinRequired(Exception):
    """The profile is PIN-locked and nobody was there to type the PIN."""


@dataclass
class SyncResult:
    added: int = 0
    merged: int = 0
    profiles: list[str] = field(default_factory=list)
    leaving_notices: int = 0


def item_to_parsed(item: dict) -> tuple[ParsedTitle, int | None]:
    """Map one entry of Netflix's viewing-activity JSON to our parsed title."""
    if item.get("seriesTitle") or item.get("series"):
        label = item.get("seasonDescriptor")
        parsed = ParsedTitle(
            name=item.get("seriesTitle") or item.get("title", "").split(": ")[0],
            kind="series",
            season_label=label,
            season_number=season_number_from_label(label) if label else None,
            episode_title=item.get("episodeTitle") or item.get("videoTitle"),
        )
        return parsed, item.get("series")
    if item.get("title"):
        parsed = parse_title(item["title"])
        return parsed, item.get("movieID") if parsed.kind == "movie" else None
    raise ValueError(f"Unrecognised viewing item: {sorted(item)}")


def _store_items(conn: sqlite3.Connection, profile_id: int, items: list[dict], result: SyncResult) -> None:
    with conn:
        for item in items:
            parsed, netflix_id = item_to_parsed(item)
            title_id = db.get_or_create_title(conn, parsed, netflix_id=netflix_id)
            ts = datetime.fromtimestamp(item["date"] / 1000, tz=timezone.utc) if item.get("date") else None
            raw = item.get("title") or parsed.name
            new = db.upsert_viewing(
                conn, profile_id=profile_id, title_id=title_id, parsed=parsed, raw_title=raw,
                watched_on=ts.date().isoformat() if ts else item["dateStr"], source="sync",
                watched_at=ts.strftime("%Y-%m-%dT%H:%M:%SZ") if ts else None,
                duration_seconds=item.get("duration"),
                device=item.get("deviceType") and str(item["deviceType"]),
                country=item.get("country"),
            )
            if new:
                result.added += 1
            else:
                result.merged += 1


def _discover_profiles(page) -> list[tuple[str, str | None]]:
    page.goto(f"{NETFLIX}/account/profiles")
    page.wait_for_load_state("networkidle")
    found: dict[str, str] = {}
    for link in page.locator("a[href]").all():
        m = _GUID_RE.search(link.get_attribute("href") or "")
        if m and m.group(1) not in found:
            name = (link.inner_text() or "").strip().split("\n")[0].strip()
            found[m.group(1)] = name or m.group(1)
    return [(name, guid) for guid, name in found.items()]


def _is_pin_prompt(page) -> bool:
    if "/pin" in page.url.lower() or page.locator(_PIN_SELECTOR).count():
        return True
    return bool(_PIN_TEXT_RE.search(page.content()))


def _collect_viewing_activity(page, url: str, max_pages: int, pin_wait_ms: int | None = None) -> list[dict]:
    """Open the viewing-activity page and gather every JSON payload with 'viewedItems'.

    Without `pin_wait_ms` a PIN prompt raises PinRequired. With it, we wait that long for
    the user to type the PIN into the (visible) browser window.
    """
    items: list[dict] = []

    def viewed_items(response) -> list | None:
        if "json" not in (response.headers.get("content-type") or ""):
            return None
        try:
            body = response.json()
        except Exception:
            return None
        if isinstance(body, dict) and isinstance(body.get("viewedItems"), list):
            return body["viewedItems"]
        return None

    def is_activity(response) -> bool:
        return viewed_items(response) is not None

    try:
        with page.expect_response(is_activity, timeout=FIRST_RESPONSE_TIMEOUT_MS) as first:
            page.goto(url)
    except Exception:
        if not _is_pin_prompt(page):
            raise
        if pin_wait_ms is None:
            raise PinRequired(url) from None
        print("This profile is PIN-locked: type the PIN in the browser window...")
        with page.expect_response(is_activity, timeout=pin_wait_ms) as first:
            pass
    items.extend(viewed_items(first.value))
    for _ in range(max_pages):
        more = page.get_by_role("button", name=re.compile(r"show more", re.I))
        if not more.count() or not more.first.is_visible():
            break
        # Wait for the specific payload the click triggers, so a slow page is never
        # dropped or attributed to the next profile.
        try:
            with page.expect_response(is_activity, timeout=30_000) as nxt:
                more.first.click()
        except Exception:
            break
        batch = viewed_items(nxt.value)
        if not batch:
            break
        items.extend(batch)
    return items


def _check_leaving(conn: sqlite3.Connection, page, limit: int) -> int:
    """Read Netflix's "Last day to watch" notice from title pages of available titles."""
    rows = conn.execute(
        "SELECT id, netflix_id FROM titles WHERE netflix_id IS NOT NULL AND netflix_status != 'removed' "
        "ORDER BY (SELECT MAX(watched_on) FROM viewings v WHERE v.title_id = titles.id) DESC LIMIT ?",
        (limit,)).fetchall()
    found = 0
    for r in rows:
        page.goto(f"{NETFLIX}/title/{r['netflix_id']}")
        page.wait_for_load_state("domcontentloaded")
        m = _LEAVING_RE.search(page.content())
        if not m:
            continue
        text = m.group(1)
        fmt = "%B %d, %Y" if "," in text else "%B %d"
        leaving = datetime.strptime(text, fmt)
        if "," not in text:
            now = datetime.now()
            leaving = leaving.replace(year=now.year + (leaving.month < now.month))
        with conn:
            record_leaving_notice(conn, r["id"], leaving.date().isoformat())
        found += 1
    return found


def _sync_profile(conn, page, name, guid, max_pages, result, pin_wait_ms=None) -> None:
    url = f"{NETFLIX}/settings/viewed/{guid}" if guid else f"{NETFLIX}/viewingactivity"
    items = _collect_viewing_activity(page, url, max_pages, pin_wait_ms)
    profile_id = db.get_or_create_profile(conn, name, guid)
    _store_items(conn, profile_id, items, result)
    result.profiles.append(f"{name} ({len(items)} items)")


def sync(conn: sqlite3.Connection, session_path: Path, profiles: list[str] | None = None,
         max_pages: int = 200, check_leaving: int = 0, headless: bool = True,
         pin_headless: bool = False) -> SyncResult:
    """Sync every profile. PIN-locked ones are retried at the end in a visible window
    (`pin_headless` exists for tests only), where you type the PIN yourself."""
    if not session_path.exists():
        raise SystemExit("No saved session. Run: pynetflix login")
    result = SyncResult()
    with _playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(storage_state=str(session_path))
        page = context.new_page()

        targets = _discover_profiles(page)
        if profiles:
            targets = [t for t in targets if t[0] in profiles]
        if not targets:
            # Couldn't read the profile list; fall back to whichever profile is active.
            targets = [((profiles or ["default"])[0], None)]

        locked: list[tuple[str, str | None]] = []
        for name, guid in targets:
            try:
                _sync_profile(conn, page, name, guid, max_pages, result)
            except PinRequired:
                locked.append((name, guid))

        if locked:
            print(f"PIN-locked profiles: {', '.join(n for n, _ in locked)}. Opening a browser window...")
            pin_browser = p.chromium.launch(headless=pin_headless)
            pin_page = pin_browser.new_context(storage_state=str(session_path)).new_page()
            for name, guid in locked:
                try:
                    _sync_profile(conn, pin_page, name, guid, max_pages, result, PIN_WAIT_MINUTES * 60_000)
                except Exception as e:
                    result.profiles.append(f"{name} (skipped: PIN not entered, {type(e).__name__})")
            pin_browser.close()

        if check_leaving:
            result.leaving_notices = _check_leaving(conn, page, check_leaving)

        # Keep cookies fresh so the session lasts.
        context.storage_state(path=str(session_path))
        browser.close()
    return result
