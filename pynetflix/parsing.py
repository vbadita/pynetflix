"""Turn Netflix's flat title strings into show / season / episode parts.

Netflix exports titles like "Dark: Season 1: Secrets" or
"Stranger Things: Stranger Things 4: Chapter One" or just "The Irishman".
There is no explicit movie/series flag, so we use the season marker.
"""

import re
from dataclasses import dataclass

_WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}

_SEASON_RE = re.compile(
    r"^(?:season|series|part|volume|vol\.?|book|chapter|collection|temporada|staffel|saison|sezonul)"
    r"\s+(\d+|" + "|".join(_WORD_NUMBERS) + r")$",
    re.IGNORECASE,
)
_SINGLE_SEASON_RE = re.compile(r"^(limited series|miniseries|mini-series)$", re.IGNORECASE)
_EPISODE_NUM_RE = re.compile(r"\s*\((?:episode|ep\.?)\s*(\d+)\)\s*$", re.IGNORECASE)


@dataclass
class ParsedTitle:
    name: str                       # show or movie name
    kind: str                       # "movie" or "series"
    season_label: str | None = None
    season_number: int | None = None
    episode_title: str | None = None
    episode_number: int | None = None


def season_number_from_label(label: str) -> int | None:
    m = _SEASON_RE.match(label.strip())
    if m:
        value = m.group(1).lower()
        return int(value) if value.isdigit() else _WORD_NUMBERS[value]
    if _SINGLE_SEASON_RE.match(label.strip()):
        return 1
    return None


def _is_season_marker(segment: str, show: str) -> bool:
    segment = segment.strip()
    if season_number_from_label(segment) is not None:
        return True
    # "Stranger Things: Stranger Things 4: ..." style.
    return bool(re.fullmatch(re.escape(show) + r"\s+\d+", segment, re.IGNORECASE))


def parse_title(raw: str) -> ParsedTitle:
    raw = raw.strip()
    episode_number = None
    m = _EPISODE_NUM_RE.search(raw)
    if m:
        episode_number = int(m.group(1))
        raw = raw[: m.start()].strip()

    parts = [p.strip() for p in raw.split(": ")]
    for i in range(1, len(parts) - 1):
        show = ": ".join(parts[:i])
        if _is_season_marker(parts[i], show):
            label = parts[i]
            number = season_number_from_label(label)
            if number is None:  # "Show 4" form
                number = int(label.rsplit(" ", 1)[1])
            return ParsedTitle(
                name=show,
                kind="series",
                season_label=label,
                season_number=number,
                episode_title=": ".join(parts[i + 1:]),
                episode_number=episode_number,
            )

    if len(parts) >= 3:
        # No recognisable season marker, but three segments is almost always a series.
        return ParsedTitle(
            name=parts[0],
            kind="series",
            season_label=parts[1],
            episode_title=": ".join(parts[2:]),
            episode_number=episode_number,
        )
    if episode_number is not None and len(parts) == 2:
        return ParsedTitle(name=parts[0], kind="series", episode_title=parts[1],
                           episode_number=episode_number)
    # Treat "Mission: Impossible" style titles as movies; enrichment can fix it later.
    return ParsedTitle(name=raw, kind="movie")


def normalize(name: str) -> str:
    """Key used to match the same title across sources."""
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def parse_duration(value: str | None) -> int | None:
    """'01:02:03' -> 3723 seconds."""
    if not value:
        return None
    parts = value.strip().split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    seconds = 0
    for n in nums:
        seconds = seconds * 60 + n
    return seconds
