"""Problem candidates — the query layer behind the problem_candidates MCP
tool and the Problem Queue page.

An incident is one case. A problem is the thing that keeps causing cases.
This module reads case metadata and proposes candidate problem records from
three deterministic signals:

  recurrence      the same Product/Type pair at the same property, min_cases
                  or more times inside the window
  repeat_contact  the same unit coming back: each case within repeat_days of
                  the one before it, min_repeats or more cases in the chain
  burst           a Product/Type pair whose weekly count ran above the median
                  of its own previous baseline_weeks weeks

Metadata only: product, type, account, unit, created date, and the Jira key
if one was recorded. No subject, no description, no LLM. A case with no
Product can't join a candidate, so the unclassified share is reported next
to the candidates instead of being left for the reader to guess.

Every candidate carries its cases' Lightning URLs, so a count is one click
from the records that prove or disprove it.
"""

import re
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from statistics import median
from zoneinfo import ZoneInfo

import sfcli

LOCAL_TZ = ZoneInfo("America/New_York")

# Product values that mean "nobody classified this". The picklist has a
# literal Undefined option alongside plain nulls.
UNCLASSIFIED_PRODUCTS = {None, "", "Undefined"}
# A Type that says the product is known but the issue isn't. These still
# group, but the candidate is tagged so a pile of "Other" isn't mistaken
# for one issue.
WEAK_TYPES = {None, "", "Other"}

CASES_SOQL = (
    "SELECT Id, CaseNumber, CreatedDate, IsClosed, AccountId, Account.Name, "
    "Account.Parent.Name, Unit_Number__c, Unit_Number__r.Name, "
    "Product_Level_1__c, Type_Level_2__c, Jira_Information__c "
    "FROM Case WHERE CreatedDate >= {since} ORDER BY CreatedDate ASC"
)

# Unit records that stand in for "no real unit": a resident the PMS never
# matched, a prospect on a self-guided tour, an agent who picked the
# default. Repeat contacts on one of these still count, but they point at
# the integration more than at a resident, so they're tagged.
_PLACEHOLDER_UNIT_RE = re.compile(r"^\s*(|0+|undefined|unknown|n/?a|none|tbd|-+)\s*$", re.IGNORECASE)

# Jira_Information__c is a hand-typed textarea: usually one or more browse
# URLs, sometimes a release note or a link to somewhere that isn't Jira.
# Only a browse URL counts as a ticket. A bare KEY-123 pattern also matches
# firmware versions and model numbers, and a wrong "ticket exists" is worse
# than a missed one.
_JIRA_URL_RE = re.compile(r"https?://\S+?/browse/([A-Z][A-Z0-9]+-\d+)")


def _parse_sf_datetime(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%f%z")


def parse_jira(text: str | None) -> tuple[dict[str, str | None], bool]:
    """Return ({ticket key: browse URL}, note_only). note_only is true when
    the field has text but no ticket URL in it."""
    text = (text or "").strip()
    if not text:
        return {}, False
    keys: dict[str, str] = {}
    for match in _JIRA_URL_RE.finditer(text):
        keys.setdefault(match.group(1), match.group(0))
    return keys, not keys


def is_placeholder_unit(name: str | None) -> bool:
    return name is None or bool(_PLACEHOLDER_UNIT_RE.match(name))


def normalize(record: dict, instance_url: str) -> dict:
    """Flatten one SOQL row into the plain dict build_report works on. The
    Jira field's text is reduced to its ticket keys here and goes no
    further."""
    account = record.get("Account") or {}
    keys, note_only = parse_jira(record.get("Jira_Information__c"))
    return {
        "caseNumber": record["CaseNumber"],
        "created": _parse_sf_datetime(record["CreatedDate"]),
        "closed": bool(record.get("IsClosed")),
        "accountId": record.get("AccountId"),
        "property": account.get("Name"),
        "parent": (account.get("Parent") or {}).get("Name"),
        "unitId": record.get("Unit_Number__c"),
        "unit": (record.get("Unit_Number__r") or {}).get("Name"),
        "product": record.get("Product_Level_1__c"),
        "type": record.get("Type_Level_2__c"),
        "jira": keys,
        "jiraNoteOnly": note_only,
        "url": f"{instance_url}/lightning/r/Case/{record['Id']}/view",
    }


def _week_of(moment: datetime) -> date:
    """Monday of the local week the moment falls in."""
    day = moment.astimezone(LOCAL_TZ).date()
    return day - timedelta(days=day.weekday())


def _week_start(monday: date) -> datetime:
    return datetime.combine(monday, time.min, tzinfo=LOCAL_TZ)


def history_start_for(now: datetime, window_days: int, baseline_weeks: int) -> datetime:
    """How far back to fetch so every week inside the window has a full
    baseline behind it: the Monday of the window's first week, minus the
    baseline."""
    window_start = now - timedelta(days=window_days)
    return _week_start(_week_of(window_start) - timedelta(weeks=baseline_weeks))


def _pct(part: int, whole: int) -> float:
    return round(100 * part / whole, 1) if whole else 0.0


def _local_iso(moment: datetime) -> str:
    return moment.astimezone(LOCAL_TZ).isoformat()


def _candidate(signal: str, cases: list[dict], **extra) -> dict:
    cases = sorted(cases, key=lambda c: (c["created"], c["caseNumber"]))
    properties: dict[str | None, dict] = {}
    for c in cases:
        entry = properties.setdefault(
            c["accountId"],
            {"name": c["property"] or "(no property)", "parent": c["parent"], "count": 0},
        )
        entry["count"] += 1
    tickets: dict[str, dict] = {}
    for c in cases:
        for key, url in c["jira"].items():
            tickets.setdefault(key, {"key": key, "url": url, "cases": 0})["cases"] += 1
    return {
        "signal": signal,
        **extra,
        "count": len(cases),
        "openCount": sum(1 for c in cases if not c["closed"]),
        "propertyCount": sum(1 for account in properties if account),
        "properties": sorted(properties.values(), key=lambda p: (-p["count"], p["name"])),
        "unitCount": len({c["unitId"] for c in cases if c["unitId"]}),
        "firstSeen": _local_iso(cases[0]["created"]),
        "lastSeen": _local_iso(cases[-1]["created"]),
        "jira": {
            "tickets": sorted(tickets.values(), key=lambda t: (-t["cases"], t["key"])),
            "linkedCases": sum(1 for c in cases if c["jira"]),
            "noteOnlyCases": sum(1 for c in cases if c["jiraNoteOnly"]),
        },
        "cases": [
            {
                "caseNumber": c["caseNumber"],
                "created": _local_iso(c["created"]),
                "closed": c["closed"],
                "property": c["property"],
                "unit": c["unit"],
                "jira": sorted(c["jira"]),
                "url": c["url"],
            }
            for c in cases
        ],
    }


def _recurrences(cases: list[dict], min_cases: int) -> list[dict]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for c in cases:
        if c["accountId"]:
            groups[(c["accountId"], c["product"], c["type"])].append(c)
    found = [
        _candidate(
            "recurrence", rows,
            product=product, type=type_, weakType=type_ in WEAK_TYPES,
            property=rows[0]["property"], parent=rows[0]["parent"],
        )
        for (_account, product, type_), rows in groups.items()
        if len(rows) >= min_cases
    ]
    found.sort(key=lambda c: (
        -c["count"], c["property"] or "", c["product"], c["type"] or "", c["cases"][0]["caseNumber"],
    ))
    return found


def _repeat_contacts(
    cases: list[dict], window_start: datetime, repeat_days: int, min_repeats: int
) -> list[dict]:
    by_unit: dict[str, list[dict]] = defaultdict(list)
    for c in cases:
        if c["unitId"]:
            by_unit[c["unitId"]].append(c)
    gap = timedelta(days=repeat_days)
    found = []
    for rows in by_unit.values():
        rows.sort(key=lambda c: (c["created"], c["caseNumber"]))
        chains, chain = [], [rows[0]]
        for c in rows[1:]:
            if c["created"] - chain[-1]["created"] <= gap:
                chain.append(c)
            else:
                chains.append(chain)
                chain = [c]
        chains.append(chain)
        for chain in chains:
            # A chain that was already running when the window opened is
            # kept whole, as long as the unit came back inside the window.
            if len(chain) < min_repeats or chain[-1]["created"] < window_start:
                continue
            span = chain[-1]["created"] - chain[0]["created"]
            found.append(_candidate(
                "repeat_contact", chain,
                unit=chain[0]["unit"], placeholderUnit=is_placeholder_unit(chain[0]["unit"]),
                property=chain[0]["property"], parent=chain[0]["parent"], spanDays=span.days,
                pairs=sorted({f"{c['product']} / {c['type'] or '(no type)'}" for c in chain}),
            ))
    found.sort(key=lambda c: (
        -c["count"], c["spanDays"], c["property"] or "", c["unit"] or "", c["cases"][0]["caseNumber"],
    ))
    return found


def _bursts(
    cases: list[dict], now: datetime, window_start: datetime, history_start: datetime,
    baseline_weeks: int, burst_ratio: float, burst_min_excess: int,
) -> list[dict]:
    weekly: dict[tuple, dict[date, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for c in cases:
        weekly[(c["product"], c["type"])][_week_of(c["created"])].append(c)

    # Only whole weeks inside the window are judged. The week in progress is
    # still filling up, so it's left out rather than compared short.
    current_week = _week_of(now)
    week = _week_of(window_start)
    if _week_start(week) < window_start:
        week += timedelta(weeks=1)
    judged = []
    while week < current_week:
        judged.append(week)
        week += timedelta(weeks=1)

    found = []
    for (product, type_), weeks in weekly.items():
        flagged, rows = [], []
        for week in judged:
            prior = [week - timedelta(weeks=i) for i in range(1, baseline_weeks + 1)]
            if _week_start(prior[-1]) < history_start:
                continue  # not enough history behind this week to call it
            baseline = median(len(weeks.get(p, ())) for p in prior)
            count = len(weeks.get(week, ()))
            if count - baseline >= burst_min_excess and count >= burst_ratio * baseline:
                flagged.append({
                    "weekOf": week.isoformat(),
                    "count": count,
                    "baseline": baseline,
                    "ratio": round(count / baseline, 1) if baseline else None,
                })
                rows.extend(weeks[week])
        if flagged:
            found.append(_candidate(
                "burst", rows,
                product=product, type=type_, weakType=type_ in WEAK_TYPES,
                weeks=flagged,
                peakExcess=max(w["count"] - w["baseline"] for w in flagged),
            ))
    found.sort(key=lambda c: (-c["peakExcess"], c["product"], c["type"] or ""))
    return found


def build_report(
    cases: list[dict],
    now: datetime,
    *,
    window_days: int = 90,
    min_cases: int = 5,
    repeat_days: int = 14,
    min_repeats: int = 3,
    baseline_weeks: int = 8,
    burst_ratio: float = 2.0,
    burst_min_excess: int = 5,
    history_start: datetime | None = None,
) -> dict:
    """Group normalized cases into problem candidates. Pure: no I/O, and
    `now` is passed in, so the same input always yields the same report.

    `cases` may reach back before the window; the older ones only feed the
    burst baselines and the front end of a repeat-contact chain. `history_start` is how far back `cases` is known to be
    complete (defaults to the earliest case), so a week the data doesn't
    fully cover is never counted as a quiet week."""
    if min(window_days, min_cases, min_repeats, baseline_weeks, burst_min_excess) < 1:
        raise ValueError("window and thresholds must all be at least 1")
    if repeat_days < 0 or burst_ratio < 1:
        raise ValueError("repeat_days must be >= 0 and burst_ratio >= 1")
    window_start = now - timedelta(days=window_days)
    if history_start is None:
        history_start = min((c["created"] for c in cases), default=now)

    in_window = [c for c in cases if window_start <= c["created"] <= now]
    classified = [c for c in in_window if c["product"] not in UNCLASSIFIED_PRODUCTS]
    classified_history = [
        c for c in cases
        if c["product"] not in UNCLASSIFIED_PRODUCTS and c["created"] <= now
    ]

    total = len(in_window)
    unclassified = total - len(classified)
    weak = sum(1 for c in classified if c["type"] in WEAK_TYPES)
    return {
        "window": {
            "days": window_days,
            "start": _local_iso(window_start),
            "end": _local_iso(now),
            "weekInProgress": _week_of(now).isoformat(),
        },
        "thresholds": {
            "minCases": min_cases,
            "repeatDays": repeat_days,
            "minRepeats": min_repeats,
            "baselineWeeks": baseline_weeks,
            "burstRatio": burst_ratio,
            "burstMinExcess": burst_min_excess,
        },
        "coverage": {
            "cases": total,
            "unclassified": unclassified,
            "unclassifiedPct": _pct(unclassified, total),
            "weakType": weak,
            "weakTypePct": _pct(weak, total),
            "noProperty": sum(1 for c in classified if not c["accountId"]),
            "noUnit": sum(1 for c in classified if not c["unitId"]),
            "jiraLinked": sum(1 for c in in_window if c["jira"]),
            "jiraNoteOnly": sum(1 for c in in_window if c["jiraNoteOnly"]),
        },
        "recurrence": _recurrences(classified, min_cases),
        "repeat_contact": _repeat_contacts(
            classified_history, window_start, repeat_days, min_repeats
        ),
        "burst": _bursts(
            classified_history, now, window_start, history_start,
            baseline_weeks, burst_ratio, burst_min_excess,
        ),
    }


def _instance_url() -> str:
    return (sfcli.sf(["org", "display"]).get("instanceUrl") or "").rstrip("/")


def fetch_report(window_days: int = 90, as_of: datetime | None = None, **thresholds) -> dict:
    """Query the org and build the report. One SELECT over Case.

    Pass `as_of` to rebuild the queue as it would have read on an earlier
    date, which is how a candidate gets checked against an investigation
    that was done by hand. Open/closed and Jira keys are still today's
    values; only the case set and the dates move."""
    now = as_of or datetime.now(timezone.utc)
    baseline_weeks = thresholds.get("baseline_weeks", 8)
    history_start = history_start_for(now, window_days, baseline_weeks)
    since = history_start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    instance_url = _instance_url()
    cases = [
        normalize(record, instance_url)
        for record in sfcli.query(CASES_SOQL.format(since=since))
    ]
    report = build_report(
        cases, now, window_days=window_days, history_start=history_start, **thresholds
    )
    return {
        "generatedAt": _local_iso(datetime.now(timezone.utc)),
        "instanceUrl": instance_url,
        **report,
    }
