"""Always-on SLA dashboard over sla.fetch_snapshot().

Run:  SF_TARGET_ORG=production python3 dashboard.py
Then pin http://localhost:8787 as a browser tab or HTML Shelf live-URL entry.

Stdlib only. No background threads: each request re-fetches if the cached
snapshot is older than REFRESH_SECONDS, otherwise serves the cache, and the
page meta-refreshes on the same interval. A failed fetch keeps the last good
snapshot on screen with an error banner rather than blanking the glass.
"""

import html
import json
import os
import traceback
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

import sla

PORT = 8787
REFRESH_SECONDS = 300

# The MCP server targets production explicitly in its registration; the
# dashboard defaults to the same org so `python3 dashboard.py` just works.
# Read-only by construction either way.
os.environ.setdefault("SF_TARGET_ORG", "production")

_cache = {"snapshot": None, "fetched_at": None, "error": None}


def _snapshot() -> dict | None:
    age = (
        (datetime.now() - _cache["fetched_at"]).total_seconds()
        if _cache["fetched_at"]
        else None
    )
    if age is None or age > REFRESH_SECONDS:
        try:
            _cache["snapshot"] = sla.fetch_snapshot()
            _cache["error"] = None
        except Exception:
            _cache["error"] = traceback.format_exc(limit=1)
        _cache["fetched_at"] = datetime.now()
    return _cache["snapshot"]


CSS = """
:root {
  --bg: #f7f6f3; --surface: #ffffff; --ink: #1a1a19; --ink-2: #5f5e5a;
  --ink-3: #8a8985; --line: #e4e2dc;
  --critical: #d03b3b; --serious: #ec835a; --warning: #fab219; --good: #0ca30c;
}
* { box-sizing: border-box; margin: 0; }
body { background: var(--bg); color: var(--ink); font: 14px/1.45 -apple-system, "Segoe UI", sans-serif; padding: 24px; }
header { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 18px; }
h1 { font-size: 18px; font-weight: 650; }
.meta { color: var(--ink-3); font-size: 12px; }
.error { background: #fdf0ef; border: 1px solid var(--critical); border-radius: 8px; padding: 10px 14px; margin-bottom: 16px; font-size: 13px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin-bottom: 22px; }
.tile { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 14px 16px; }
.tile .n { font-size: 30px; font-weight: 700; font-variant-numeric: tabular-nums; }
.tile .label { color: var(--ink-2); font-size: 12px; margin-top: 2px; }
.tile .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; }
section { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 16px 18px; margin-bottom: 16px; }
section h2 { font-size: 13px; font-weight: 650; text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 4px; }
.rollup { color: var(--ink-2); font-size: 12px; margin-bottom: 10px; }
table { width: 100%; border-collapse: collapse; }
th { text-align: left; color: var(--ink-3); font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; font-weight: 600; padding: 6px 10px 6px 0; border-bottom: 1px solid var(--line); }
td { padding: 7px 10px 7px 0; border-bottom: 1px solid var(--line); vertical-align: top; }
tr:last-child td { border-bottom: none; }
td.when { white-space: nowrap; font-variant-numeric: tabular-nums; font-weight: 600; }
a { color: inherit; text-decoration: none; border-bottom: 1px dotted var(--ink-3); }
a:hover { border-bottom-style: solid; }
.subject { color: var(--ink-2); max-width: 420px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; display: inline-block; vertical-align: bottom; }
.empty { color: var(--ink-3); font-size: 13px; padding: 6px 0; }
details summary { cursor: pointer; font-size: 13px; font-weight: 650; text-transform: uppercase; letter-spacing: 0.04em; }
details[open] summary { margin-bottom: 10px; }
"""

BUCKETS = [
    ("breaching_soon", "Breaching soon", "warning", "countdown"),
    ("breached_today", "Breached today", "critical", "overdue"),
    ("breached_week", "Breached this week", "serious", "overdue"),
]


def _when(row: dict, mode: str) -> str:
    m = row["minutesRemaining"]
    if mode == "countdown":
        return f"{m // 60}h {m % 60:02d}m left" if m >= 60 else f"{m}m left"
    m = -m
    if m < 60:
        return f"{m}m ago"
    if m < 48 * 60:
        return f"{m // 60}h {m % 60:02d}m ago"
    return f"{m // (24 * 60)}d ago"


def _target_local(row: dict) -> str:
    return datetime.fromisoformat(row["target"]).strftime("%a %b %-d, %-I:%M %p")


def _rollup(rows: list[dict]) -> str:
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["owner"] or "(no owner)"] = counts.get(r["owner"] or "(no owner)", 0) + 1
    return " · ".join(
        f"{html.escape(owner)} {n}"
        for owner, n in sorted(counts.items(), key=lambda kv: -kv[1])
    )


def _table(rows: list[dict], mode: str) -> str:
    if not rows:
        return '<div class="empty">Nothing here right now.</div>'
    ordered = rows if mode == "countdown" else sorted(rows, key=lambda r: r["minutesRemaining"])
    body = "".join(
        f"<tr><td class='when'>{_when(r, mode)}</td>"
        f"<td><a href='{html.escape(r['url'])}' target='_blank'>{r['caseNumber']}</a></td>"
        f"<td><span class='subject'>{html.escape(r['subject'] or '')}</span></td>"
        f"<td>{html.escape(r['priority'] or '')}</td>"
        f"<td>{html.escape(r['owner'] or '')}</td>"
        f"<td>{html.escape(r['milestone'])}</td>"
        f"<td class='when'>{_target_local(r)}</td></tr>"
        for r in ordered
    )
    return (
        "<table><thead><tr><th>Clock</th><th>Case</th><th>Subject</th><th>Priority</th>"
        "<th>Owner</th><th>Milestone</th><th>Target</th></tr></thead>"
        f"<tbody>{body}</tbody></table>"
    )


def render(snap: dict | None, error: str | None) -> str:
    parts = [
        f"<style>{CSS}</style>",
        f'<meta http-equiv="refresh" content="{REFRESH_SECONDS}">',
        "<title>SLA Watch</title>",
    ]
    if snap is None:
        parts.append(f'<div class="error">No snapshot yet. {html.escape(error or "")}</div>')
        return "\n".join(parts)

    generated = datetime.fromisoformat(snap["generatedAt"]).strftime("%a %b %-d, %-I:%M %p")
    parts.append(
        f"<header><h1>SLA Watch</h1>"
        f'<div class="meta">{html.escape(snap["instanceUrl"])} · refreshed {generated} · auto-refresh {REFRESH_SECONDS // 60}m</div></header>'
    )
    if error:
        parts.append(
            f'<div class="error">Last refresh failed — showing previous snapshot. {html.escape(error)}</div>'
        )

    c = snap["counts"]
    tile_specs = [
        ("breaching_soon", "⏳ Breaching soon", "var(--warning)"),
        ("breached_today", "🔴 Breached today", "var(--critical)"),
        ("breached_week", "🟠 Breached this week", "var(--serious)"),
        ("stale_backlog", "🗄 Stale backlog (>7d)", "var(--ink-3)"),
        ("waiting", "💤 Waiting on resident", "var(--ink-3)"),
    ]
    tiles = "".join(
        f'<div class="tile"><div class="n">{c[key]}</div>'
        f'<div class="label"><span class="dot" style="background:{color}"></span>{label}</div></div>'
        for key, label, color in tile_specs
    )
    perf = snap.get("responsePerf")
    if perf and perf["total"]:
        med = perf["medianDeltaMinutes"]
        med_text = f"{abs(med) // 60}h {abs(med) % 60:02d}m" if abs(med) >= 60 else f"{abs(med)}m"
        met_color = "var(--good)" if perf["metPct"] >= 80 else "var(--critical)"
        tiles += (
            f'<div class="tile"><div class="n">{perf["metPct"]}%</div>'
            f'<div class="label"><span class="dot" style="background:{met_color}"></span>'
            f'✉️ Met first-response SLA ({perf["windowDays"]}d)</div></div>'
            f'<div class="tile"><div class="n">{med_text}</div>'
            f'<div class="label"><span class="dot" style="background:var(--ink-3)"></span>'
            f'📐 Median response delta, {"late" if med > 0 else "early"} ({perf["windowDays"]}d)</div></div>'
        )
    parts.append(f'<div class="tiles">{tiles}</div>')

    for key, label, _color, mode in BUCKETS:
        rows = snap[key]
        parts.append(
            f"<section><h2>{label} ({len(rows)})</h2>"
            + (f'<div class="rollup">{_rollup(rows)}</div>' if rows else "")
            + _table(rows, mode)
            + "</section>"
        )

    perf = snap.get("responsePerf")
    if perf and perf["total"]:
        med = perf["medianDeltaMinutes"]
        med_label = (
            f"{abs(med) // 60}h {abs(med) % 60:02d}m " + ("late" if med > 0 else "early")
            if abs(med) >= 60
            else f"{abs(med)}m " + ("late" if med > 0 else "early")
        )
        perf_rows = "".join(
            f"<tr><td class='when'>{r['deltaMinutes'] // 60}h {r['deltaMinutes'] % 60:02d}m late</td>"
            f"<td><a href='{html.escape(r['url'])}' target='_blank'>{r['caseNumber']}</a></td>"
            f"<td><span class='subject'>{html.escape(r['subject'] or '')}</span></td>"
            f"<td>{html.escape(r['owner'] or '')}</td>"
            f"<td class='when'>{datetime.fromisoformat(r['responded']).strftime('%a %b %-d, %-I:%M %p')}</td></tr>"
            for r in perf["worst"]
        )
        no_email = (
            f" · {perf['noEmail']} with no outbound email excluded (phone/SMS)"
            if perf.get("noEmail")
            else ""
        )
        parts.append(
            f"<section><h2>First response performance — last {perf['windowDays']} days</h2>"
            f'<div class="rollup">{perf["total"]} first responses, measured from the '
            f"case's <b>first outbound email</b> (milestone CompletionDate is unreliable "
            f"here — auto-completion misses some send paths, and manual sweeps stamp "
            f'late times onto cases answered in minutes) · <b>{perf["metPct"]}% met SLA</b> '
            f'({perf["met"]} of {perf["total"]}) · median {med_label}{no_email}. '
            "Deltas are wall-clock, so business-hours pauses in the entitlement "
            "process read as later here than Salesforce counts them.</div>"
            "<table><thead><tr><th>Missed by</th><th>Case</th><th>Subject</th>"
            f"<th>Owner</th><th>Responded</th></tr></thead><tbody>{perf_rows}</tbody></table>"
            "</section>"
        )

    waiting = snap["waiting"]
    parts.append(
        f"<section><h2>Waiting on resident ({len(waiting)})</h2>"
        '<div class="rollup">Paused clocks, not fires — the resident owes the next move. '
        "Violated here means the follow-up nudge window passed.</div>"
        + _table(waiting, "overdue")
        + "</section>"
    )

    stale = snap["stale_backlog"]
    parts.append(
        f"<section><details><summary>Stale backlog — breached more than 7 days ago ({len(stale)})</summary>"
        + (f'<div class="rollup">{_rollup(stale)}</div>' if stale else "")
        + _table(stale, "overdue")
        + "</details></section>"
    )
    return "\n".join(parts)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/data.json":
            snap = _snapshot()
            body = json.dumps({"error": _cache["error"], "snapshot": snap}).encode()
            content_type = "application/json"
        elif self.path == "/":
            body = render(_snapshot(), _cache["error"]).encode()
            content_type = "text/html; charset=utf-8"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    print(
        f"SLA Watch on http://localhost:{PORT} "
        f"(org: {os.environ['SF_TARGET_ORG']}, refresh every {REFRESH_SECONDS}s)"
    )
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
