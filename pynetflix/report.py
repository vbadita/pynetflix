"""Render the Wrapped recap as a single self-contained HTML file."""

from html import escape


def _bars(items: list[dict], label: str, value: str, unit: str) -> str:
    if not items:
        return "<p class='muted'>Nothing yet.</p>"
    top = max(i[value] for i in items) or 1
    rows = []
    for i in items:
        width = max(2, round(100 * i[value] / top))
        rows.append(
            f"<li><span class='name'>{escape(str(i[label]))}</span>"
            f"<span class='bar'><span style='width:{width}%'></span></span>"
            f"<span class='val'>{i[value]} {unit}</span></li>")
    return "<ul class='bars'>" + "".join(rows) + "</ul>"


def render_wrapped(w: dict) -> str:
    who = escape(w["profile"] or "Everyone")
    period = f"{w['period']['from']} → {w['period']['to']}"
    binge = w["biggest_binge"]
    tiles = [
        ("Hours watched", f"{w['total_hours']}{'*' if w['duration_is_estimated'] else ''}"),
        ("Titles", w["titles"]),
        ("Movies / series", f"{w['movies']} / {w['series']}"),
        ("Longest streak", f"{w['longest_streak_days']} days"),
        ("Favourite day", w["favourite_weekday"] or "–"),
        ("Night-owl share", f"{round(100 * w['night_owl_share'])}%" if w["night_owl_share"] is not None else "–"),
    ]
    tiles_html = "".join(f"<div class='tile'><div class='k'>{escape(k)}</div>"
                         f"<div class='v'>{escape(str(v))}</div></div>" for k, v in tiles)
    binge_html = (f"<p>Biggest binge: <b>{binge['episodes']} episodes of {escape(binge['title'])}</b> "
                  f"on {binge['date']}.</p>" if binge else "")
    busiest = w["busiest_day"]
    busiest_html = (f"<p>Busiest day: <b>{busiest['date']}</b> with {busiest['hours']} hours.</p>"
                    if busiest else "")
    removed = w["watched_then_removed"]
    removed_html = ("<h2>Gone from Netflix since</h2><p>" + ", ".join(escape(t) for t in removed) + "</p>"
                    if removed else "")
    note = ("<p class='muted'>* Some durations are estimated from TMDB runtimes. "
            "Import the personal data export for exact watch time.</p>" if w["duration_is_estimated"] else "")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Netflix Wrapped</title>
<style>
:root {{ --bg:#fafafa; --fg:#141414; --muted:#6b6b6b; --card:#fff; --accent:#e50914; --track:#eee; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg:#141414; --fg:#f5f5f5; --muted:#a3a3a3; --card:#1f1f1f; --accent:#ff3b45; --track:#2c2c2c; }}
}}
body {{ margin:0; background:var(--bg); color:var(--fg); font:16px/1.5 system-ui, sans-serif; }}
main {{ max-width:760px; margin:0 auto; padding:32px 16px 64px; }}
h1 {{ font-size:2.2rem; margin:0; }} h1 span {{ color:var(--accent); }}
h2 {{ margin-top:2rem; font-size:1.15rem; }}
.muted {{ color:var(--muted); }}
.tiles {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(150px, 1fr)); gap:12px; margin-top:24px; }}
.tile {{ background:var(--card); border-radius:12px; padding:14px 16px; }}
.tile .k {{ color:var(--muted); font-size:.85rem; }} .tile .v {{ font-size:1.5rem; font-weight:700; }}
.bars {{ list-style:none; padding:0; margin:0; }}
.bars li {{ display:grid; grid-template-columns:minmax(0, 2fr) 3fr auto; gap:10px; align-items:center; padding:5px 0; }}
.bars .name {{ overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
.bar {{ background:var(--track); border-radius:99px; height:10px; overflow:hidden; }}
.bar span {{ display:block; height:100%; background:var(--accent); border-radius:99px; }}
.val {{ color:var(--muted); font-variant-numeric:tabular-nums; font-size:.9rem; }}
</style></head>
<body><main>
<h1><span>Wrapped</span> · {who}</h1>
<p class="muted">{period}</p>
<div class="tiles">{tiles_html}</div>
{binge_html}{busiest_html}
<h2>Top titles</h2>{_bars(w['top_titles'], 'title', 'hours', 'h')}
<h2>Top genres</h2>{_bars(w['top_genres'], 'genre', 'hours', 'h')}
{removed_html}
{note}
</main></body></html>"""
