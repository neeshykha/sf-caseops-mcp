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

---

## Setup

Requires the [Salesforce CLI](https://developer.salesforce.com/tools/salesforcecli)
authed to at least one org, and Python 3.10+.

```bash
git clone https://github.com/neeshykha/sf-caseops-mcp
cd sf-caseops-mcp
python3 -m venv .venv && ./.venv/bin/pip install mcp
```

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

## Why this exists

I run support operations for an IoT SaaS platform. The queries above are ones
I ran weekly as one-off `sf` CLI scripts — each report a script, each new
question a new script. Exposing the org as agent-callable tools inverts that:
one server, and the agent composes the questions. This repo is the pattern,
extracted: swap the case-ops tools for your own object model and the
delegated-auth/read-only structure carries over unchanged.
