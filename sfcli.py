"""Shared sf CLI plumbing for sf-caseops-mcp.

Everything here shells out to the `sf` CLI, so auth lives entirely in the
CLI's keychain: nothing in this repo sees, stores, or transmits credentials.
Read-only is enforced by construction — the only subcommands wrapped are
`data query`, `sobject describe`, and `org display`, none of which can
modify data.
"""

import json
import os
import shutil
import subprocess

SF_TIMEOUT = 120


def sf(args: list[str]) -> dict:
    """Run an sf CLI command with --json and return the parsed result."""
    if shutil.which("sf") is None:
        raise RuntimeError("The `sf` CLI is not installed or not on PATH.")
    target = os.environ.get("SF_TARGET_ORG")
    cmd = ["sf", *args, "--json"]
    if target:
        cmd += ["--target-org", target]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=SF_TIMEOUT)
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(
            f"sf returned non-JSON output (exit {proc.returncode}): "
            f"{proc.stderr.strip() or proc.stdout[:500]}"
        )
    if payload.get("status") != 0:
        raise RuntimeError(payload.get("message") or json.dumps(payload)[:500])
    return payload["result"]


def query(soql: str) -> list[dict]:
    result = sf(["data", "query", "--query", soql])
    records = result.get("records", [])
    for r in records:
        _strip_attributes(r)
    return records


def _strip_attributes(record: dict) -> None:
    record.pop("attributes", None)
    for v in record.values():
        if isinstance(v, dict):
            _strip_attributes(v)
