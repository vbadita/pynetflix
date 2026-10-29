# pynetflix

**Your own Netflix account as a local API.** Netflix has no public API for your viewing history.
pynetflix pulls it into a local SQLite database for every profile. It adds genres, cast and
runtimes, tracks **when titles you watched were removed from Netflix**, and serves it all as a
CLI, a REST API and an MCP server for Claude and other assistants.

Everything runs on your machine. Your password and your history never leave it.

## Install

```bash
pip install -e ".[all]"            # from a clone of this repo
playwright install chromium        # only needed for automatic sync
```

Get a free TMDB API key at https://www.themoviedb.org/settings/api, then:

```bash
export TMDB_API_KEY=...            # v3 key or v4 read token
export NETFLIX_COUNTRY=RO          # the country your Netflix account is in (default US)
```

## Quick start

```bash
# 1. Load your history (pick one or more)
pynetflix import NetflixViewingHistory.csv --profile "Valentin"   # netflix.com/viewingactivity → Download all
pynetflix import netflix-report.zip                               # Account → Download your personal information
pynetflix login && pynetflix sync                                 # automatic, all profiles

# 2. Add metadata + Netflix availability
pynetflix enrich

# 3. Use it
pynetflix removed                    # what you watched that's no longer on Netflix
pynetflix export history.csv         # full history incl. the removed_from_netflix column
pynetflix wrapped --year 2026 --html wrapped.html
pynetflix serve                      # REST API on http://127.0.0.1:8000 (docs at /docs)
```

Importing the same data twice is safe. Viewings are matched across sources, so the CSV, the
data export and sync can all be combined. The data export gives the richest data: exact
timestamps, watch time, device and episode numbers.

## "Removed from Netflix" column

Every title gets these fields (in `titles`, `titles_view`, the API and `export`):

| column | meaning |
|---|---|
| `netflix_status` | `available`, `removed` or `unknown` (not checked yet) |
| `removed_from_netflix` | the date it left Netflix: exact when known, otherwise the day it was first seen gone |
| `removed_precision` | `exact`, or `between <last seen> and <detected>` |
| `netflix_last_seen_available` | last date it was known to be on Netflix (your own last watch counts) |
| `netflix_leaving_on` | announced "Last day to watch", when captured |

Netflix doesn't publish removal dates, so pynetflix works them out:

1. **Your history is evidence.** If you watched something on 2024-02-01, it was on Netflix that day.
2. **Availability checks.** `enrich` and `check-availability` ask TMDB (JustWatch data) whether each
   title is on Netflix in `NETFLIX_COUNTRY`. The first check that finds it gone sets the removal date.
3. **Exact dates.** `pynetflix sync --check-leaving 50` reads Netflix's own "Last day to watch"
   notice on your 50 most recently watched titles. If a title disappears after that date, the
   notice becomes the exact removal date.

Run `check-availability` daily and the removal window shrinks to a single day:

```bash
# crontab -e
0 9 * * * TMDB_API_KEY=... NETFLIX_COUNTRY=RO pynetflix check-availability
```

On the first run, titles removed long ago get a wide window, from your last watch to today.
Availability differs by country, so set `NETFLIX_COUNTRY` to your account's country.

## Features

| command | what it does |
|---|---|
| `status` | What's loaded, what's been removed, and what to run next |
| `import FILE` | Viewing Activity CSV (`--profile` needed) or the personal-data export ZIP |
| `login` / `sync` | Log in once in a real browser window, then pull history for all profiles |
| `enrich` | TMDB genres, cast, runtime, season/episode counts, episode numbers, availability |
| `check-availability` | Re-check which watched titles are still on Netflix |
| `removed` / `leaving` | Titles that left Netflix / have an announced last day |
| `history` / `export` | Query or dump your history (JSON / CSV) |
| `ratings` | Your thumbs down / up / "love this" ratings, imported from the data export |
| `wrapped` | Spotify-Wrapped-style recap per year or month, with a shareable HTML page |
| `abandoned` | Shows you stopped watching, flagging those with a new season out |
| `shared` | Overlap between profiles and "watch together" suggestions |
| `letterboxd OUT.csv` | Letterboxd import file (movies) |
| `trakt login` / `trakt push` | Sync history to Trakt (needs `TRAKT_CLIENT_ID` / `TRAKT_CLIENT_SECRET`) |
| `serve` | REST API (FastAPI): `/profiles`, `/history`, `/titles`, `/titles/removed`, `/ratings`, `/wrapped`, ... |
| `mcp` | MCP server for assistants |

### Use it from Claude

```bash
claude mcp add pynetflix -- pynetflix mcp
```

Then ask things like "what did I watch last weekend?", "which shows did I abandon that have a new
season?" or "what did I watch that's no longer on Netflix?".

### Python

```python
from pynetflix import db, stats
conn = db.connect("~/.pynetflix/pynetflix.db")
stats.history(conn, profile="Valentin", since="2026-01-01")
stats.wrapped(conn, year=2026)
```

## About sync

`pynetflix login` opens a normal browser window and you log in yourself. pynetflix only saves the
session cookies to `~/.pynetflix/session.json`, readable only by you. `sync` then reads each
profile's viewing-activity page in a headless browser.

Netflix changes its website without notice, so all of that logic lives in
`pynetflix/sync.py`. If sync breaks, that is the file to fix, and the file imports keep working
in the meantime. PIN-locked profiles are not supported yet.

Netflix's terms of use don't allow automated access. Use sync only on your own account, and
don't run it as a service for others. The import commands use files Netflix gives you and don't
have this issue.

## Development

```bash
pip install -e ".[dev,sync]"
pytest
```

Data lives in `~/.pynetflix/` (override with `PYNETFLIX_HOME`).
