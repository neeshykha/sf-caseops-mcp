"""Problem Queue — a static page over problems.fetch_report().

Run:  python3 problem_page.py            # last 90 days -> ~/Downloads/problem-queue.html
      python3 problem_page.py --days 30
      python3 problem_page.py --as-of 2026-06-30 --out /tmp/june.html

Stdlib only, one self-contained file, nothing left running. The page leads
with how much of the window was unclassified, because that share is the
ceiling on everything below it.
"""

import argparse
import html
import os
from datetime import datetime, time, timedelta
from pathlib import Path

import problems

DEFAULT_OUT = Path.home() / "Downloads" / "problem-queue.html"

# Same default as the dashboard: the registered MCP server targets
# production, so `python3 problem_page.py` reads the same org. Read-only by
# construction either way.
os.environ.setdefault("SF_TARGET_ORG", "production")

FAVICON = (
    "data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22>"
    "<text y=%22.9em%22 font-size=%2290%22>🧩</text></svg>"
)

CSS = """
:root {
  --bg: #f7f6f3; --surface: #ffffff; --ink: #1a1a19; --ink-2: #5f5e5a;
  --ink-3: #8a8985; --line: #e4e2dc; --chip: #f1efe9;
  --critical: #d03b3b; --serious: #ec835a; --warning: #fab219; --good: #0ca30c;
  --warn-bg: #fff7e0;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #171716; --surface: #20201f; --ink: #ecebe7; --ink-2: #b3b2ad;
    --ink-3: #85847f; --line: #33322f; --chip: #2a2a28; --warn-bg: #33290c;
  }
}
* { box-sizing: border-box; margin: 0; }
body { background: var(--bg); color: var(--ink); font: 14px/1.45 -apple-system, "Segoe UI", sans-serif; padding: 24px; max-width: 1180px; margin: 0 auto; }
header { display: flex; justify-content: space-between; align-items: baseline; flex-wrap: wrap; gap: 6px; margin-bottom: 18px; }
h1 { font-size: 18px; font-weight: 650; }
.meta { color: var(--ink-3); font-size: 12px; }
.gap { background: var(--warn-bg); border: 1px solid var(--warning); border-radius: 10px; padding: 14px 18px; margin-bottom: 16px; display: flex; gap: 18px; align-items: center; flex-wrap: wrap; }
.gap .n { font-size: 34px; font-weight: 700; font-variant-numeric: tabular-nums; }
.gap p { flex: 1; min-width: 260px; font-size: 13px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin-bottom: 22px; }
.tile { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 14px 16px; }
.tile .n { font-size: 30px; font-weight: 700; font-variant-numeric: tabular-nums; }
.tile .label { color: var(--ink-2); font-size: 12px; margin-top: 2px; }
section { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 16px 18px; margin-bottom: 16px; }
section h2 { font-size: 13px; font-weight: 650; text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 4px; }
.rollup { color: var(--ink-2); font-size: 12px; margin-bottom: 10px; max-width: 860px; }
details.cand { border-top: 1px solid var(--line); padding: 9px 0; }
details.cand > summary { cursor: pointer; display: grid; grid-template-columns: 44px minmax(0, 1fr) auto; gap: 10px; align-items: baseline; list-style: none; }
details.cand > summary::-webkit-details-marker { display: none; }
.count { font-size: 18px; font-weight: 700; font-variant-numeric: tabular-nums; text-align: right; }
.title { display: block; font-weight: 600; }
.sub { display: block; color: var(--ink-2); font-size: 12px; margin-top: 1px; }
.tags { display: flex; gap: 6px; flex-wrap: wrap; justify-content: flex-end; }
.tag { font-size: 11px; border-radius: 999px; padding: 2px 9px; background: var(--chip); color: var(--ink-2); white-space: nowrap; }
.tag.none { border: 1px solid var(--serious); background: transparent; color: var(--ink); }
.tag.has { border: 1px solid var(--good); background: transparent; color: var(--ink); }
.tag.weak { border: 1px dashed var(--ink-3); background: transparent; }
.body { margin: 10px 0 4px 54px; font-size: 13px; }
.body h3 { font-size: 11px; color: var(--ink-3); text-transform: uppercase; letter-spacing: 0.05em; font-weight: 600; margin: 10px 0 4px; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chip { background: var(--chip); border-radius: 6px; padding: 2px 8px; font-variant-numeric: tabular-nums; white-space: nowrap; }
.chip.open { box-shadow: inset 0 0 0 1px var(--serious); }
.chip small { color: var(--ink-3); }
a { color: inherit; text-decoration: none; border-bottom: 1px dotted var(--ink-3); }
a:hover { border-bottom-style: solid; }
button.copy { font: inherit; font-size: 12px; color: var(--ink); background: var(--chip); border: 1px solid var(--line); border-radius: 6px; padding: 3px 9px; cursor: pointer; margin-top: 10px; }
.empty { color: var(--ink-3); font-size: 13px; padding: 6px 0; }
footer { color: var(--ink-3); font-size: 12px; max-width: 860px; margin-top: 6px; }
"""

JS = """
document.addEventListener('click', function (e) {
  var b = e.target.closest('button.copy');
  if (!b) return;
  navigator.clipboard.writeText(b.dataset.prompt).then(function () {
    var was = b.textContent; b.textContent = 'Copied';
    setTimeout(function () { b.textContent = was; }, 1200);
  });
});
"""

SECTIONS = [
    (
        "recurrence", "Recurring at one property",
        "The same Product / Type pair at the same property, {minCases} or more times in the window. "
        "Many units means the property; one unit means a device or a resident.",
    ),
    (
        "burst", "Bursts above the pair's own baseline",
        "A whole week where a pair ran at {burstRatio}x or more the median of its previous "
        "{baselineWeeks} weeks, and at least {burstMinExcess} cases over it. Org-wide, so the "
        "properties list shows whether it's one site or everywhere. Sorted by the biggest single-week "
        "jump. The week in progress isn't judged.",
    ),
    (
        "repeat_contact", "Repeat contacts from one unit",
        "{minRepeats} or more cases from the same unit, each within {repeatDays} days of the one "
        "before. Product and type can differ: the signal is that the first answer didn't hold. "
        "A chain that started before the window is shown whole.",
    ),
]


def _e(value) -> str:
    return html.escape(str(value if value is not None else ""))


def _day(iso: str) -> str:
    return datetime.fromisoformat(iso).strftime("%b %-d")


def _pair(c: dict) -> str:
    return f"{c['product']} / {c['type'] or '(no type)'}"


def _title(c: dict) -> tuple[str, str]:
    """(title, subtitle) for a candidate row."""
    seen = f"{_day(c['firstSeen'])} to {_day(c['lastSeen'])}"
    open_note = f" · {c['openCount']} still open" if c["openCount"] else ""
    if c["signal"] == "recurrence":
        where = c["property"] or "(no property)"
        parent = f" ({c['parent']})" if c.get("parent") else ""
        units = f"{c['unitCount']} unit{'s' if c['unitCount'] != 1 else ''}"
        return f"{_pair(c)} at {where}", f"{where}{parent} · {units} · {seen}{open_note}"
    if c["signal"] == "burst":
        weeks = ", ".join(
            f"week of {datetime.fromisoformat(w['weekOf']).strftime('%b %-d')}: {w['count']} vs {w['baseline']:g}"
            for w in c["weeks"]
        )
        props = f"{c['propertyCount']} propert{'ies' if c['propertyCount'] != 1 else 'y'}"
        return _pair(c), f"{weeks} · {props} · {seen}{open_note}"
    where = c["property"] or "(no property)"
    return (
        f"Unit {c['unit'] or '?'} at {where}",
        f"{len(c['pairs'])} issue type{'s' if len(c['pairs']) != 1 else ''} over "
        f"{c['spanDays']} day{'s' if c['spanDays'] != 1 else ''} · {seen}{open_note}",
    )


def _jira_tag(c: dict) -> str:
    tickets = c["jira"]["tickets"]
    if tickets:
        label = tickets[0]["key"] + (f" +{len(tickets) - 1}" if len(tickets) > 1 else "")
        return f'<span class="tag has">Jira: {_e(label)} on {c["jira"]["linkedCases"]} of {c["count"]}</span>'
    return '<span class="tag none">No Jira key recorded</span>'


def _candidate(c: dict) -> str:
    title, sub = _title(c)
    tags = [_jira_tag(c)]
    if c.get("weakType"):
        tags.append('<span class="tag weak">weak type</span>')
    body = []

    if c["signal"] != "recurrence":
        body.append("<h3>Affected properties</h3><div class='chips'>" + "".join(
            f"<span class='chip'>{_e(p['name'])} <small>{p['count']}</small></span>"
            for p in c["properties"]
        ) + "</div>")
    if c["signal"] == "repeat_contact":
        body.append("<h3>Issue types</h3><div class='chips'>" + "".join(
            f"<span class='chip'>{_e(p)}</span>" for p in c["pairs"]
        ) + "</div>")

    tickets = c["jira"]["tickets"]
    if tickets:
        body.append("<h3>Jira keys recorded on these cases</h3><div class='chips'>" + "".join(
            "<span class='chip'>"
            + f"<a href='{_e(t['url'])}' target='_blank'>{_e(t['key'])}</a>"
            + f" <small>{t['cases']} case{'s' if t['cases'] != 1 else ''}</small></span>"
            for t in tickets
        ) + "</div>")
    if c["jira"]["noteOnlyCases"]:
        n = c["jira"]["noteOnlyCases"]
        body.append(
            f"<div class='sub'>{n} case{'s have' if n != 1 else ' has'} text in the Jira field "
            "with no ticket key in it.</div>"
        )

    body.append(f"<h3>Cases ({c['count']})</h3><div class='chips'>" + "".join(
        f"<span class='chip{'' if case['closed'] else ' open'}'>"
        f"<a href='{_e(case['url'])}' target='_blank'>{_e(case['caseNumber'])}</a> "
        f"<small>{_day(case['created'])}"
        + (f" · unit {_e(case['unit'])}" if case["unit"] and c["signal"] != "repeat_contact" else "")
        + (f" · {_e(', '.join(case['jira']))}" if case["jira"] else "")
        + ("" if case["closed"] else " · open")
        + "</small></span>"
        for case in reversed(c["cases"])
    ) + "</div>")

    if not tickets:
        # Newest open case if there is one, otherwise the newest: that's the
        # one with the freshest detail to build a ticket from.
        open_cases = [case for case in c["cases"] if not case["closed"]]
        pick = (open_cases or c["cases"])[-1]["caseNumber"]
        prompt = f"build me a Jira ticket for {pick}"
        body.append(
            f"<button class='copy' data-prompt='{_e(prompt)}'>Copy: {_e(prompt)}</button>"
        )

    return (
        "<details class='cand'><summary>"
        f"<span class='count'>{c['count']}</span>"
        f"<span><span class='title'>{_e(title)}</span><span class='sub'>{_e(sub)}</span></span>"
        f"<span class='tags'>{''.join(tags)}</span>"
        f"</summary><div class='body'>{''.join(body)}</div></details>"
    )


def render(report: dict) -> str:
    cov, win, thr = report["coverage"], report["window"], report["thresholds"]
    generated = datetime.fromisoformat(report["generatedAt"]).strftime("%a %b %-d %Y, %-I:%M %p")
    classified = cov["cases"] - cov["unclassified"]
    candidates = [c for key, _, _ in SECTIONS for c in report[key]]
    no_ticket = sum(1 for c in candidates if not c["jira"]["tickets"])

    parts = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>Problem Queue</title>",
        f'<link rel="icon" href="{FAVICON}">',
        f"<style>{CSS}</style></head><body>",
        "<header><h1>Problem Queue</h1>"
        f'<div class="meta">{_e(report.get("instanceUrl", ""))} · cases created '
        f"{_day(win['start'])} to {_day(win['end'])} ({win['days']} days) · built {generated}</div></header>",
        '<div class="gap">'
        f'<div class="n">{cov["unclassifiedPct"]:g}%</div>'
        f"<p><b>{cov['unclassified']:,} of {cov['cases']:,} cases have no Product</b> (blank or "
        "Undefined), so they can't join any candidate below. Everything on this page is drawn from "
        f"the other {classified:,}. A problem that mostly arrives unclassified is invisible here. "
        f"Another {cov['weakType']:,} ({cov['weakTypePct']:g}%) have a Product but a Type of Other "
        "or blank: those still group, tagged <i>weak type</i>, because a pile of Other at one "
        "property is a lead and not a diagnosis.</p></div>",
    ]

    tiles = [
        (f"{cov['cases']:,}", "Cases in window"),
        (len(report["recurrence"]), "Recurring at one property"),
        (len(report["burst"]), "Pairs that burst"),
        (len(report["repeat_contact"]), "Repeat-contact units"),
        (f"{no_ticket} of {len(candidates)}", "Candidates with no Jira key"),
    ]
    parts.append('<div class="tiles">' + "".join(
        f'<div class="tile"><div class="n">{n}</div><div class="label">{_e(label)}</div></div>'
        for n, label in tiles
    ) + "</div>")

    for key, label, blurb in SECTIONS:
        rows = report[key]
        parts.append(
            f"<section><h2>{label} ({len(rows)})</h2>"
            f'<div class="rollup">{_e(blurb.format(**thr))}</div>'
            + ("".join(_candidate(c) for c in rows) or '<div class="empty">Nothing over the threshold.</div>')
            + "</section>"
        )

    parts.append(
        "<footer><p><b>How to read the Jira tag.</b> It comes from the case's Jira Information "
        "field, which agents fill in by hand. A key means someone recorded a ticket on that case; "
        "it says nothing about whether the ticket is still open, because this page has no Jira "
        f"access. No key means none was recorded, not that none exists. In this window "
        f"{cov['jiraLinked']:,} of {cov['cases']:,} cases carry a key"
        + (f" and {cov['jiraNoteOnly']} more have text in the field with no key in it" if cov["jiraNoteOnly"] else "")
        + ".</p><p style='margin-top:6px'>Grouping uses Product, Type, account, unit, and created "
        "date only. No subjects or descriptions are read. A case can appear under more than one "
        "candidate. Open cases are outlined.</p></footer>"
    )
    parts.append(f"<script>{JS}</script></body></html>")
    return "\n".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description="Render the Problem Queue page.")
    parser.add_argument("--days", type=int, default=90, help="window in days (30-90 is the useful range)")
    parser.add_argument("--min-cases", type=int, default=5)
    parser.add_argument("--repeat-days", type=int, default=14)
    parser.add_argument("--as-of", help="YYYY-MM-DD: build the queue as of the end of that day")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    as_of = None
    if args.as_of:
        # Midnight closing the day, so an as-of Sunday counts its own week
        # as finished.
        day = datetime.strptime(args.as_of, "%Y-%m-%d").date() + timedelta(days=1)
        as_of = datetime.combine(day, time.min, tzinfo=problems.LOCAL_TZ)
    report = problems.fetch_report(
        args.days, as_of=as_of, min_cases=args.min_cases, repeat_days=args.repeat_days
    )
    args.out.write_text(render(report), encoding="utf-8")
    counts = ", ".join(f"{len(report[key])} {key}" for key, _, _ in SECTIONS)
    print(f"Wrote {args.out} ({counts}; {report['coverage']['unclassifiedPct']}% unclassified)")


if __name__ == "__main__":
    main()
