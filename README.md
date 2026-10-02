# sf-caseops-mcp

A read-only MCP server that turns Salesforce case operations into tools an AI
agent can call. Point Claude (Desktop or Code) at your org and ask questions in
plain English: "where's the backlog right now," "show me escalated cases from
this week," "created vs closed for the last month."

Built from real support-ops practice: the tool surface is the set of queries a
support operations manager actually runs — queue volumes, case lookups, weekly
created/closed trend — not a generic API wrapper.

---

## Design decisions

**Auth is delegated entirely to the `sf` CLI.** Every tool shells out to `sf`,
so credentials live in the CLI's keychain. The server never sees, stores, or
transmits a password or token — if `sf org list` works, the server works.

**Read-only by construction, not by promise.** The server only wraps three
subcommands: `sf data query`, `sf sobject describe`, and `sf org display`.
None of them can modify data. On top of that, the raw SOQL tool rejects
anything that isn't a SELECT, and identifier inputs (object names, case
numbers, statuses) are validated before they touch a query. Giving an LLM
write access to a production CRM is a decision that deserves its own design
conversation — this server deliberately doesn't open that door.

**Org targeting is explicit and inspectable.** Set `SF_TARGET_ORG` to an org
alias (falls back to the sf CLI default org). The `org_info` tool reports
which org is connected and whether it's a sandbox, so the agent — and you —
can always verify before trusting an answer.

---

## Tools

| Tool | What it does |
|------|--------------|
| `org_info` | Which org am I connected to? Alias, username, instance, sandbox or not |
| `soql_query` | Arbitrary read-only SOQL SELECT |
| `describe_object` | Field names, types, and picklist values for any object |
| `case_lookup` | Full detail on one case by number, including recent comments |
| `recent_cases` | Cases from the last N days, optionally filtered by status |
| `queue_volumes` | Open case counts by queue/owner — the "where's the backlog" view |
| `case_volume_report` | Weekly created vs closed vs net, for trend spotting |
| `sla_risk_report` | Entitlement milestone risk: pending first-response clocks, fresh breaches, paused waiting-on-customer milestones |
| `problem_candidates` | Candidate problem records: Product/Type pairs recurring at one property, weekly bursts over a pair's own baseline, units that keep coming back |

---

## Setup

Requires the [Salesforce CLI](https://developer.salesforce.com/tools/salesforcecli)
authed to at least one org, and Python 3.10+.

```bash
git clone https://github.com/neeshykha/sf-caseops-mcp
cd sf-caseops-mcp
python3 -m venv .venv && ./.venv/bin/pip install "mcp>=1.28,<2"
```

The version bound matters. The MCP Python SDK released v2.0.0 on 2026-07-28
alongside the stateless spec revision, and it renamed `FastMCP` to `MCPServer`,
so a bare `pip install mcp` now resolves to an SDK this server does not import
against. This server uses none of the features that revision reworked — no
sampling, roots, logging, or elicitation — so there is no urgency to port; v1.x
is in security-fix-only maintenance and a v2 port is a deliberate change for
later, not something to acquire by accident.

**Claude Code:**

```bash
claude mcp add sf-caseops -e SF_TARGET_ORG=sandbox -- \
  /path/to/sf-caseops-mcp/.venv/bin/python /path/to/sf-caseops-mcp/server.py
```

**Claude Desktop** (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "sf-caseops": {
      "command": "/path/to/sf-caseops-mcp/.venv/bin/python",
      "args": ["/path/to/sf-caseops-mcp/server.py"],
      "env": { "SF_TARGET_ORG": "sandbox" }
    }
  }
}
```

---

## What a session looks like

Example data below is synthetic.

> **You:** where's the backlog right now?
>
> **Claude:** *(calls `queue_volumes`)* Tier 1 Queue is carrying most of it —
> 214 open cases. After that it drops off fast: the top three individual
> owners hold 38 combined. Want me to break Tier 1 down by age or priority?
>
> **You:** how did last month trend?
>
> **Claude:** *(calls `case_volume_report`)* Volume is stable but you're
> falling slightly behind: created outpaced closed in three of the last four
> weeks, net +23 overall. The week of the 8th was the outlier — 312 created
> against 267 closed.

The useful part isn't any single query — it's that follow-up questions
compose. "Break that down by priority" becomes a `soql_query` call the agent
writes itself, using `describe_object` to get the field names right.

---

## SLA Watch — the always-on dashboard

The same SLA query layer that powers `sla_risk_report` also drives a
zero-dependency local dashboard:

```bash
python3 dashboard.py
```

Then pin `http://localhost:8787` as a browser tab. It re-queries the org at
most every five minutes, auto-refreshes the page on the same interval, and
survives a failed refresh by keeping the last good snapshot on screen with an
error banner.

The layout encodes a support-ops opinion about what deserves attention:

- **Breaching soon** — pending first-response clocks, soonest first. The only
  bucket where minutes matter.
- **Breached today / this week** — fresh violations, oldest first, with a
  per-owner rollup so you can see which queue is underwater.
- **Waiting on resident** — its own quiet section. A paused clock where the
  customer owes the next move is not the same kind of problem as a missed
  first response, and mixing them buries the real fires.
- **Stale backlog** — milestones breached more than 7 days ago, collapsed to
  a count. These are zombie cases; they're real debt, but they'd drown the
  actionable signal if listed inline.

Every case number deep-links to the record in Lightning, so any number on the
board is one click from the source of truth that verifies it.

---

## Problem Queue — incidents into problem candidates

An incident is one case. A problem is whatever keeps producing them. Support
teams are good at the first and tend to find the second by accident: someone
notices they've typed the same reply four times this week. `problem_candidates`
does that noticing on purpose, and `problem_page.py` renders the result as one
static HTML file:

```bash
python3 problem_page.py              # last 90 days -> ~/Downloads/problem-queue.html
python3 problem_page.py --days 30
python3 problem_page.py --as-of 2026-06-30 --out june.html
```

Three signals, all deterministic:

- **Recurrence**: the same Product/Type pair at the same property, five or
  more times in the window. The unit count tells you which kind of problem
  it is. Twenty cases across twenty units is the building; five cases at one
  unit is a device or a resident.
- **Burst**: a pair whose week ran at twice the median of its own previous
  eight weeks, and at least five cases over it. Each pair is judged against
  itself, so a noisy category doesn't drown a quiet one that tripled. The
  median matters: one bad week doesn't raise the bar for the next.
- **Repeat contact**: three or more cases from one unit, each within 14 days
  of the one before. Product and type can differ here, because the signal is
  that the first answer didn't hold.

Every candidate shows its count, the properties it touches, first and last
seen, and whether a Jira key is recorded on any of its cases. Every case
number links to the record in Lightning. A candidate is a claim, and the
cases are how you check it.

**What it reads, and what it doesn't.** Product, type, account, unit,
created date, and the Jira field. No subjects, no descriptions, no model
reading case text. That's a deliberate trade: nothing a customer wrote
leaves the org or lands on the page, and the same input gives the same
answer every time. The cost is that grouping is only as good as the
classification agents did at intake.

So the page leads with that cost. A case with no Product can't join any
candidate, and the share of cases in that state sits at the top in the
largest type on the page, above the first candidate. Cases with a Product
but a Type of "Other" still group, tagged as weak, because a pile of Other at
one property is a lead and not a diagnosis. If a problem mostly arrives
unclassified, this tool won't see it, and it says so.

**The Jira tag is a record, not a status.** In my org, agents paste the
ticket's browse URL into a text field on the case. The tool pulls the key
out of that URL and ignores everything else in the field, including bare
`ABC-123` patterns, which also match firmware versions. A key means someone
recorded a ticket. It doesn't mean the ticket is open, because this server
has no Jira access and I'd rather not add a second credential to find out.
No key means none was recorded, which is its own finding more often than
you'd hope.

**Checked against work done by hand.** `--as-of` rebuilds the queue as it
would have read on an earlier date. I ran it against a defect I'd already
dated manually from years of case history, one that showed up under two
categories. The burst signal flagged one of them in the same month the hand
investigation put the change in volume. It never flagged the other: that
category climbed for three months, peaking at 1.6x to 1.75x its baseline
against a 2x bar, because a rolling median follows a slow ramp up. Bursts
catch jumps, not drifts. And nothing here could see the first reports, filed
ten months earlier: two cases, recognizable only from what the customer
wrote. That's the boundary of a metadata-only approach, and the reason this
produces candidates for a person to review and not problem records.

The field names in `CASES_SOQL` (`Product_Level_1__c`, `Type_Level_2__c`,
`Unit_Number__c`, `Jira_Information__c`) are my org's. Swap them for your own
taxonomy; the grouping in `build_report` only sees the normalized rows.

The grouping logic is pure and tested against synthetic fixtures:

```bash
python3 -m unittest discover -s tests
```

---

## Why this exists

I run support operations for an IoT SaaS platform. The queries above are ones
I ran weekly as one-off `sf` CLI scripts — each report a script, each new
question a new script. Exposing the org as agent-callable tools inverts that:
one server, and the agent composes the questions. This repo is the pattern,
extracted: swap the case-ops tools for your own object model and the
delegated-auth/read-only structure carries over unchanged.
