import pytest

from pynetflix.parsing import normalize, parse_duration, parse_title


@pytest.mark.parametrize("raw, name, kind, season, episode, ep_num", [
    ("Dark: Season 1: Secrets", "Dark", "series", 1, "Secrets", None),
    ("Dark: Season 1: Secrets (Episode 1)", "Dark", "series", 1, "Secrets", 1),
    ("Stranger Things: Stranger Things 4: Chapter One: The Hellfire Club",
     "Stranger Things", "series", 4, "Chapter One: The Hellfire Club", None),
    ("Our Planet: Limited Series: One Planet", "Our Planet", "series", 1, "One Planet", None),
    ("Money Heist: Part 3: We Need to Talk", "Money Heist", "series", 3, "We Need to Talk", None),
    ("Avatar: The Last Airbender: Book Two: The Avatar State",
     "Avatar: The Last Airbender", "series", 2, "The Avatar State", None),
    ("The Irishman", "The Irishman", "movie", None, None, None),
    ("Mission: Impossible", "Mission: Impossible", "movie", None, None, None),
])
def test_parse_title(raw, name, kind, season, episode, ep_num):
    p = parse_title(raw)
    assert (p.name, p.kind, p.season_number, p.episode_title, p.episode_number) == \
        (name, kind, season, episode, ep_num)


def test_helpers():
    assert normalize("Mission: Impossible") == "mission impossible"
    assert parse_duration("01:02:03") == 3723
    assert parse_duration("") is None
