"""sf-caseops-mcp — read-only Salesforce case operations as MCP tools.

Every tool shells out to the `sf` CLI, so auth lives entirely in the CLI's
keychain: the server never sees, stores, or transmits credentials. Read-only
is enforced by construction — the only subcommands this server wraps are
`data query`, `sobject describe`, and `org display`, none of which can
modify data.
"""

import json
import re
from collections import defaultdict
from datetime import datetime, timedelta

from mcp.server.fastmcp import FastMCP

import sla
from sfcli import query as _query, sf as _sf

mcp = FastMCP("sf-caseops")

DEFAULT_CASE_FIELDS = (
    "CaseNumber, Subject, Status, Priority, Origin, CreatedDate, "
    "ClosedDate, Owner.Name, Contact.Name, Account.Name"
)


_SELECT_RE = re.compile(r"^\s*SELECT\b", re.IGNORECASE)


@mcp.tool()
def org_info() -> str:
    """Show which Salesforce org this server is connected to (alias, username,
    instance URL, and whether it is a sandbox). Call this first if there is any
    doubt about which org you are querying."""
    result = _sf(["org", "display"])
    info = {
        "alias": result.get("alias"),
        "username": result.get("username"),
        "instanceUrl": result.get("instanceUrl"),
        "isSandbox": "sandbox" in (result.get("instanceUrl") or "").lower(),
        "connectedStatus": result.get("connectedStatus"),
    }
    return json.dumps(info, indent=2)


@mcp.tool()
def soql_query(query: str) -> str:
    """Run a read-only SOQL SELECT query and return matching records as JSON.
    Use describe_object first if you are unsure of field names. Non-SELECT
    input is rejected."""
    if not _SELECT_RE.match(query):
        raise ValueError("Only SELECT queries are allowed — this server is read-only.")
    records = _query(query)
    return json.dumps({"count": len(records), "records": records}, indent=2)


@mcp.tool()
def describe_object(object_name: str) -> str:
    """Describe a Salesforce object's fields: API name, label, type, and
    picklist values where applicable. Defaults are most useful for Case, but
    any queryable object works (Contact, Account, custom objects...)."""
    if not re.fullmatch(r"[A-Za-z0-9_]+", object_name):
        raise ValueError(f"Invalid object name: {object_name!r}")
    result = _sf(["sobject", "describe", "--sobject", object_name])
    fields = [
        {
            "name": f["name"],
            "label": f["label"],
            "type": f["type"],
            **(
                {"picklistValues": [v["value"] for v in f["picklistValues"] if v["active"]]}
                if f["type"] in ("picklist", "multipicklist")
                else {}
            ),
        }
        for f in result.get("fields", [])
    ]
    return json.dumps({"object": object_name, "fieldCount": len(fields), "fields": fields}, indent=2)


@mcp.tool()
def case_lookup(case_number: str) -> str:
    """Look up a single case by its case number and return full detail,
    including description and the five most recent comments."""
    if not re.fullmatch(r"[0-9]+", case_number):
        raise ValueError(f"Invalid case number: {case_number!r}")
    records = _query(
        f"SELECT {DEFAULT_CASE_FIELDS}, Description, "
        f"(SELECT CommentBody, CreatedDate, CreatedBy.Name FROM CaseComments "
        f"ORDER BY CreatedDate DESC LIMIT 5) "
        f"FROM Case WHERE CaseNumber = '{case_number}'"
    )
    if not records:
        return json.dumps({"found": False, "caseNumber": case_number})
    return json.dumps({"found": True, "case": records[0]}, indent=2)


@mcp.tool()
def recent_cases(days: int = 7, status: str = "", limit: int = 50) -> str:
    """List cases created in the last N days, newest first. Optionally filter
    by exact status (e.g. 'New', 'Escalated', 'Closed')."""
    if not 1 <= days <= 365:
        raise ValueError("days must be between 1 and 365")
    if not 1 <= limit <= 200:
        raise ValueError("limit must be between 1 and 200")
    where = f"CreatedDate = LAST_N_DAYS:{days}"
    if status:
        if not re.fullmatch(r"[A-Za-z0-9 _/-]+", status):
            raise ValueError(f"Invalid status: {status!r}")
        where += f" AND Status = '{status}'"
    records = _query(
        f"SELECT {DEFAULT_CASE_FIELDS} FROM Case WHERE {where} "
        f"ORDER BY CreatedDate DESC LIMIT {limit}"
    )
    return json.dumps({"count": len(records), "cases": records}, indent=2)


@mcp.tool()
def queue_volumes() -> str:
    """Count open cases grouped by owner (queues and users), highest volume
    first. The at-a-glance 'where is the backlog' view."""
    records = _query(
        "SELECT Owner.Name ownerName, COUNT(Id) openCases FROM Case "
        "WHERE IsClosed = false GROUP BY Owner.Name ORDER BY COUNT(Id) DESC"
    )
    return json.dumps({"openCasesByOwner": records}, indent=2)


@mcp.tool()
def case_volume_report(weeks: int = 4) -> str:
    """Weekly created-vs-closed case counts for the last N weeks, oldest week
    first. Each week runs Monday through Sunday."""
    if not 1 <= weeks <= 52:
        raise ValueError("weeks must be between 1 and 52")
    days = weeks * 7
    created = _query(
        f"SELECT DAY_ONLY(convertTimezone(CreatedDate)) d, COUNT(Id) n FROM Case "
        f"WHERE CreatedDate = LAST_N_DAYS:{days} "
        f"GROUP BY DAY_ONLY(convertTimezone(CreatedDate))"
    )
    closed = _query(
        f"SELECT DAY_ONLY(convertTimezone(ClosedDate)) d, COUNT(Id) n FROM Case "
        f"WHERE ClosedDate = LAST_N_DAYS:{days} "
        f"GROUP BY DAY_ONLY(convertTimezone(ClosedDate))"
    )
    weekly: dict[str, dict[str, int]] = defaultdict(lambda: {"created": 0, "closed": 0})
    for rows, key in ((created, "created"), (closed, "closed")):
        for row in rows:
            day = datetime.strptime(row["d"], "%Y-%m-%d").date()
            monday = day - timedelta(days=day.weekday())
            weekly[monday.isoformat()][key] += row["n"]
    report = [
        {"weekOf": week, **counts, "net": counts["created"] - counts["closed"]}
        for week, counts in sorted(weekly.items())
    ]
    return json.dumps({"weeks": report}, indent=2)


@mcp.tool()
def sla_risk_report(include_stale: bool = False, limit_per_bucket: int = 25) -> str:
    """SLA milestone risk snapshot for open cases: pending first-response
    clocks soonest-first (breaching_soon), breaches inside 24h
    (breached_today), breaches 1-7 days old (breached_week), and paused
    Waiting-on-Resident milestones (waiting). Milestones breached more than
    7 days ago are counted but only listed when include_stale is true —
    that bucket is mostly zombie cases. Every row carries the case's
    Lightning URL."""
    if not 1 <= limit_per_bucket <= 200:
        raise ValueError("limit_per_bucket must be between 1 and 200")
    snap = sla.fetch_snapshot()
    report = {
        "generatedAt": snap["generatedAt"],
        "counts": snap["counts"],
        "responsePerf": snap["responsePerf"],
        "breaching_soon": snap["breaching_soon"][:limit_per_bucket],
        "breached_today": snap["breached_today"][:limit_per_bucket],
        "breached_week": snap["breached_week"][:limit_per_bucket],
        "waiting": snap["waiting"][:limit_per_bucket],
    }
    if include_stale:
        report["stale_backlog"] = snap["stale_backlog"][:limit_per_bucket]
    return json.dumps(report, indent=2)


if __name__ == "__main__":
    mcp.run()
