"""SLA milestone snapshot — the shared query layer behind the dashboard and
the sla_risk_report MCP tool.

Pulls every incomplete CaseMilestone on an open case and buckets it:

  breaching_soon   pending first-response clocks, soonest first
  breached_today   violated with a target inside the last 24h — actionable
  breached_week    violated 1-7 days ago
  stale_backlog    violated more than 7 days ago (zombie cases; count, don't drown)
  waiting          "Waiting on Resident" milestones — a paused clock, not a fire,
                   so they get their own section regardless of violation state

Milestone rows carry the case's Lightning URL so every number on the dashboard
is one click from the record that proves or disproves it.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import sfcli

LOCAL_TZ = ZoneInfo("America/New_York")

SNAPSHOT_SOQL = (
    "SELECT CaseId, Case.CaseNumber, Case.Subject, Case.Priority, "
    "Case.Owner.Name, MilestoneType.Name, TargetDate, IsViolated "
    "FROM CaseMilestone "
    "WHERE IsCompleted = false AND Case.IsClosed = false "
    "ORDER BY TargetDate ASC"
)

RESPONSE_PERF_DAYS = 7

RESPONSE_PERF_SOQL = (
    "SELECT CaseId, Case.CaseNumber, Case.Subject, Case.Owner.Name, "
    "TargetDate, CompletionDate "
    "FROM CaseMilestone "
    "WHERE MilestoneType.Name = 'First Response to Customer' "
    f"AND IsCompleted = true AND CompletionDate = LAST_N_DAYS:{RESPONSE_PERF_DAYS}"
)


def _parse_sf_datetime(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%f%z")


def _instance_url() -> str:
    return (sfcli.sf(["org", "display"]).get("instanceUrl") or "").rstrip("/")


def _row(record: dict, now: datetime, instance_url: str) -> dict:
    target = _parse_sf_datetime(record["TargetDate"])
    case = record["Case"]
    owner = (case.get("Owner") or {}).get("Name")
    minutes = (target - now).total_seconds() / 60
    return {
        "caseNumber": case["CaseNumber"],
        "subject": case.get("Subject"),
        "priority": case.get("Priority"),
        "owner": owner,
        "milestone": record["MilestoneType"]["Name"],
        "target": target.astimezone(LOCAL_TZ).isoformat(),
        "minutesRemaining": round(minutes),
        "violated": record["IsViolated"],
        "url": f"{instance_url}/lightning/r/Case/{record['CaseId']}/view",
    }


def fetch_snapshot() -> dict:
    now = datetime.now(timezone.utc)
    instance_url = _instance_url()
    records = sfcli.query(SNAPSHOT_SOQL)

    buckets = {
        "breaching_soon": [],
        "breached_today": [],
        "breached_week": [],
        "stale_backlog": [],
        "waiting": [],
    }
    for record in records:
        row = _row(record, now, instance_url)
        target = _parse_sf_datetime(record["TargetDate"])
        if "Waiting on Resident" in row["milestone"]:
            buckets["waiting"].append(row)
        elif not row["violated"]:
            buckets["breaching_soon"].append(row)
        elif now - target <= timedelta(days=1):
            buckets["breached_today"].append(row)
        elif now - target <= timedelta(days=7):
            buckets["breached_week"].append(row)
        else:
            buckets["stale_backlog"].append(row)

    # Oldest breach first is the actionable order everywhere except the
    # countdown bucket, which SOQL already sorted soonest-target-first.
    return {
        "generatedAt": now.astimezone(LOCAL_TZ).isoformat(),
        "instanceUrl": instance_url,
        "counts": {name: len(rows) for name, rows in buckets.items()},
        **buckets,
        "responsePerf": _response_perf(instance_url),
    }


def _response_perf(instance_url: str) -> dict:
    """How completed first responses landed against their SLA target over the
    last RESPONSE_PERF_DAYS days. Deltas are wall-clock: if the entitlement
    process pauses for business hours, a target that lapses over a weekend
    reads as later than the process considers it."""
    rows = []
    for record in sfcli.query(RESPONSE_PERF_SOQL):
        target = _parse_sf_datetime(record["TargetDate"])
        completed = _parse_sf_datetime(record["CompletionDate"])
        case = record["Case"]
        rows.append({
            "caseNumber": case["CaseNumber"],
            "subject": case.get("Subject"),
            "owner": (case.get("Owner") or {}).get("Name"),
            "target": target.astimezone(LOCAL_TZ).isoformat(),
            "completed": completed.astimezone(LOCAL_TZ).isoformat(),
            "deltaMinutes": round((completed - target).total_seconds() / 60),
            "url": f"{instance_url}/lightning/r/Case/{record['CaseId']}/view",
        })
    rows.sort(key=lambda r: -r["deltaMinutes"])
    deltas = sorted(r["deltaMinutes"] for r in rows)
    met = sum(1 for d in deltas if d <= 0)
    return {
        "windowDays": RESPONSE_PERF_DAYS,
        "total": len(rows),
        "met": met,
        "metPct": round(100 * met / len(rows)) if rows else None,
        "medianDeltaMinutes": deltas[len(deltas) // 2] if deltas else None,
        "worst": rows[:10],
    }
