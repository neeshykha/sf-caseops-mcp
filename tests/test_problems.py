"""Tests for the deterministic grouping in problems.py.

Every fixture is synthetic: made-up properties, units, case numbers, and
ticket keys. Nothing here came out of an org.

Run:  python3 -m unittest discover -s tests
"""

import random
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import problem_page  # noqa: E402
import problems  # noqa: E402

ET = problems.LOCAL_TZ
# A Wednesday. The week in progress starts Monday 2026-03-16, and the week
# of 2026-03-02 contains the spring DST change.
# Held in UTC, like the datetimes the org returns, so day arithmetic in
# these fixtures is absolute time and not Eastern wall-clock time.
NOW = datetime(2026, 3, 18, 12, 0, tzinfo=ET).astimezone(timezone.utc)
JIRA = "https://example.atlassian.net/browse/"

_serial = iter(range(1, 100000))


def case(created, product="Lock", type_="Keypad Issue", account="A1", unit="U1",
         jira=None, note_only=False, closed=True):
    n = next(_serial)
    return {
        "caseNumber": f"{n:08d}",
        "created": created.astimezone(timezone.utc),
        "closed": closed,
        "accountId": account,
        "property": f"Property {account}" if account else None,
        "parent": "Parent Co" if account else None,
        "unitId": unit,
        "unit": unit,
        "product": product,
        "type": type_,
        "jira": jira or {},
        "jiraNoteOnly": note_only,
        "url": f"https://example.my.salesforce.com/lightning/r/Case/500{n:012d}/view",
    }


def days_ago(n, **kw):
    return case(NOW - timedelta(days=n), **kw)


def in_week(monday, count, **kw):
    """`count` cases spread across the local week starting `monday`, each
    on its own unit so they can't form a repeat-contact chain."""
    start = datetime(monday.year, monday.month, monday.day, 12, 0, tzinfo=ET)
    return [
        case(start + timedelta(days=i % 7, minutes=i), unit=f"W{monday:%m%d}-{i}", **kw)
        for i in range(count)
    ]


def monday(month, day):
    return datetime(2026, month, day, tzinfo=ET).date()


class ParseJira(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(problems.parse_jira(None), ({}, False))
        self.assertEqual(problems.parse_jira("  \r\n "), ({}, False))

    def test_browse_url(self):
        keys, note_only = problems.parse_jira(JIRA + "ABC-123")
        self.assertEqual(keys, {"ABC-123": JIRA + "ABC-123"})
        self.assertFalse(note_only)

    def test_several_urls_and_trailing_note(self):
        text = f"{JIRA}ABC-1\r\n{JIRA}ABC-2\r\n\r\nPending 4.2.1"
        keys, note_only = problems.parse_jira(text)
        self.assertEqual(list(keys), ["ABC-1", "ABC-2"])
        self.assertFalse(note_only)

    def test_url_with_a_context_path(self):
        url = "https://jira.example.com/jira/browse/ABC-12"
        self.assertEqual(problems.parse_jira(url), ({"ABC-12": url}, False))

    def test_text_without_a_ticket_url_is_a_note(self):
        for text in (
            "Release 4.2.1", "https://example.slack.com/archives/C01AB2", "pending fix",
            "Firmware FW-2.3.1 pending", "ZW-700 chip", "UTF-8", "see ABC-77",
            "https://drive.example.com/d/1kQ-AB-12-x/view",
        ):
            self.assertEqual(problems.parse_jira(text), ({}, True), text)


class Normalize(unittest.TestCase):
    RECORD = {
        "Id": "500000000000001AAA",
        "CaseNumber": "00000001",
        "CreatedDate": "2026-03-10T14:30:00.000+0000",
        "IsClosed": False,
        "AccountId": "001A",
        "Account": {"Name": "Property A", "Parent": {"Name": "Parent Co"}},
        "Unit_Number__c": "a13U",
        "Unit_Number__r": {"Name": "101"},
        "Product_Level_1__c": "Lock",
        "Type_Level_2__c": "Battery",
        "Jira_Information__c": JIRA + "ABC-5\nsome free text that must not survive",
    }

    def test_flattens(self):
        row = problems.normalize(self.RECORD, "https://example.my.salesforce.com")
        self.assertEqual(row["created"], datetime(2026, 3, 10, 14, 30, tzinfo=timezone.utc))
        self.assertEqual(
            (row["property"], row["parent"], row["unit"], row["product"], row["type"]),
            ("Property A", "Parent Co", "101", "Lock", "Battery"),
        )
        self.assertEqual(
            row["url"],
            "https://example.my.salesforce.com/lightning/r/Case/500000000000001AAA/view",
        )
        self.assertFalse(row["closed"])

    def test_jira_text_is_reduced_to_keys(self):
        row = problems.normalize(self.RECORD, "https://x")
        self.assertEqual(row["jira"], {"ABC-5": JIRA + "ABC-5"})
        self.assertNotIn("free text", repr(row))

    def test_missing_lookups(self):
        bare = {**self.RECORD, "AccountId": None, "Account": None, "Unit_Number__c": None,
                "Unit_Number__r": None, "Jira_Information__c": None}
        row = problems.normalize(bare, "https://x")
        self.assertEqual((row["property"], row["parent"], row["unit"]), (None, None, None))
        self.assertEqual((row["jira"], row["jiraNoteOnly"]), ({}, False))


class Coverage(unittest.TestCase):
    def test_unclassified_and_weak_shares(self):
        cases = (
            [days_ago(i + 1, unit=f"u{i}") for i in range(5)]
            + [days_ago(1, product="Undefined", type_="Not Enough Information", unit="x1")]
            + [days_ago(2, product=None, type_=None, unit="x2")]
            + [days_ago(3, product="", type_=None, unit="x3")]
            + [days_ago(4, type_="Other", unit="x4"), days_ago(5, type_=None, unit="x5")]
        )
        cov = problems.build_report(cases, NOW)["coverage"]
        self.assertEqual(cov["cases"], 10)
        self.assertEqual((cov["unclassified"], cov["unclassifiedPct"]), (3, 30.0))
        self.assertEqual((cov["weakType"], cov["weakTypePct"]), (2, 20.0))

    def test_jira_counts(self):
        cases = [
            days_ago(1, jira={"ABC-1": JIRA + "ABC-1"}),
            days_ago(2, note_only=True, unit="u2"),
            days_ago(3, unit="u3"),
        ]
        cov = problems.build_report(cases, NOW)["coverage"]
        self.assertEqual((cov["jiraLinked"], cov["jiraNoteOnly"]), (1, 1))

    def test_empty_input(self):
        report = problems.build_report([], NOW)
        self.assertEqual(report["coverage"]["cases"], 0)
        self.assertEqual(report["coverage"]["unclassifiedPct"], 0.0)
        self.assertEqual((report["recurrence"], report["repeat_contact"], report["burst"]), ([], [], []))
        self.assertEqual(report["drift"], [])

    def test_window_edges(self):
        cases = [
            case(NOW - timedelta(days=90)),               # exactly on the edge: in
            case(NOW - timedelta(days=90, seconds=1)),    # just outside
            case(NOW + timedelta(seconds=1)),             # after an as-of date
        ]
        self.assertEqual(problems.build_report(cases, NOW)["coverage"]["cases"], 1)


class Recurrence(unittest.TestCase):
    def spread(self, n, **kw):
        # 20 days apart on distinct units: never a repeat-contact chain.
        return [days_ago(1 + i * 20 % 85, unit=f"r{i}", **kw) for i in range(n)]

    def test_threshold(self):
        self.assertEqual(problems.build_report(self.spread(4), NOW)["recurrence"], [])
        found = problems.build_report(self.spread(5), NOW)["recurrence"]
        self.assertEqual(len(found), 1)
        c = found[0]
        self.assertEqual((c["count"], c["product"], c["type"]), (5, "Lock", "Keypad Issue"))
        self.assertEqual((c["property"], c["parent"]), ("Property A1", "Parent Co"))
        self.assertEqual((c["propertyCount"], c["unitCount"]), (1, 5))
        self.assertFalse(c["weakType"])

    def test_properties_and_pairs_do_not_merge(self):
        cases = (
            self.spread(3, account="A1") + self.spread(3, account="A2")
            + self.spread(3, account="A1", type_="Battery")
        )
        self.assertEqual(problems.build_report(cases, NOW, min_cases=4)["recurrence"], [])

    def test_unclassified_never_forms_a_candidate(self):
        cases = self.spread(9, product="Undefined", type_="Not Enough Information")
        cases += self.spread(9, product=None, type_=None)
        report = problems.build_report(cases, NOW)
        self.assertEqual(report["recurrence"], [])
        self.assertEqual(report["coverage"]["unclassified"], 18)

    def test_no_account_is_skipped_and_counted(self):
        report = problems.build_report(self.spread(6, account=None), NOW)
        self.assertEqual(report["recurrence"], [])
        self.assertEqual(report["coverage"]["noProperty"], 6)

    def test_outside_window_does_not_count(self):
        cases = self.spread(4) + [days_ago(120, unit="old")]
        self.assertEqual(problems.build_report(cases, NOW)["recurrence"], [])

    def test_weak_type_is_tagged(self):
        found = problems.build_report(self.spread(5, type_="Other"), NOW)["recurrence"]
        self.assertTrue(found[0]["weakType"])

    def test_first_last_seen_open_count_and_case_order(self):
        cases = [days_ago(d, unit=f"u{d}", closed=d != 3) for d in (40, 3, 25, 61, 12)]
        c = problems.build_report(cases, NOW)["recurrence"][0]
        self.assertEqual(c["firstSeen"], (NOW - timedelta(days=61)).astimezone(ET).isoformat())
        self.assertEqual(c["lastSeen"], (NOW - timedelta(days=3)).astimezone(ET).isoformat())
        self.assertEqual(c["openCount"], 1)
        created = [row["created"] for row in c["cases"]]
        self.assertEqual(created, sorted(created))
        self.assertTrue(all("/lightning/r/Case/" in row["url"] for row in c["cases"]))

    def test_jira_rollup(self):
        cases = self.spread(5)
        cases[0]["jira"] = {"ABC-1": JIRA + "ABC-1"}
        cases[1]["jira"] = {"ABC-1": JIRA + "ABC-1", "ABC-2": JIRA + "ABC-2"}
        cases[2]["jiraNoteOnly"] = True
        jira = problems.build_report(cases, NOW)["recurrence"][0]["jira"]
        self.assertEqual(jira["tickets"], [
            {"key": "ABC-1", "url": JIRA + "ABC-1", "cases": 2},
            {"key": "ABC-2", "url": JIRA + "ABC-2", "cases": 1},
        ])
        self.assertEqual((jira["linkedCases"], jira["noteOnlyCases"]), (2, 1))

    def test_sorted_largest_first(self):
        cases = self.spread(5, account="A1") + self.spread(8, account="A2")
        found = problems.build_report(cases, NOW)["recurrence"]
        self.assertEqual([c["count"] for c in found], [8, 5])


class RepeatContact(unittest.TestCase):
    def test_chain_of_three(self):
        cases = [days_ago(d, type_=t) for d, t in ((30, "Battery"), (20, "Keypad Issue"), (10, "Battery"))]
        found = problems.build_report(cases, NOW)["repeat_contact"]
        self.assertEqual(len(found), 1)
        c = found[0]
        self.assertEqual((c["count"], c["unit"], c["spanDays"]), (3, "U1", 20))
        self.assertEqual(c["pairs"], ["Lock / Battery", "Lock / Keypad Issue"])

    def test_gap_is_inclusive_at_exactly_repeat_days(self):
        cases = [days_ago(d) for d in (29, 15, 1)]  # gaps of exactly 14 days
        self.assertEqual(len(problems.build_report(cases, NOW)["repeat_contact"]), 1)

    def test_gap_one_second_over_breaks_the_chain(self):
        cases = [
            case(NOW - timedelta(days=29, seconds=1)),
            days_ago(15),
            days_ago(1),
        ]
        self.assertEqual(problems.build_report(cases, NOW)["repeat_contact"], [])

    def test_two_separate_chains_at_one_unit(self):
        cases = [days_ago(d) for d in (80, 78, 76, 30, 28, 26, 24)]
        found = problems.build_report(cases, NOW)["repeat_contact"]
        self.assertEqual([c["count"] for c in found], [4, 3])

    def test_different_units_do_not_chain(self):
        cases = [days_ago(d, unit=f"u{d}") for d in (3, 2, 1)]
        self.assertEqual(problems.build_report(cases, NOW)["repeat_contact"], [])

    def test_unclassified_and_unitless_cases_are_left_out(self):
        cases = [days_ago(d, product="Undefined") for d in (3, 2, 1)]
        cases += [days_ago(d, unit=None) for d in (3, 2, 1)]
        self.assertEqual(problems.build_report(cases, NOW)["repeat_contact"], [])

    def test_chain_already_running_when_the_window_opened_is_kept_whole(self):
        cases = [days_ago(d) for d in (95, 92, 89)]
        found = problems.build_report(cases, NOW)["repeat_contact"]
        self.assertEqual([c["count"] for c in found], [3])
        self.assertEqual(problems.build_report(cases, NOW)["coverage"]["cases"], 1)

    def test_chain_that_ended_before_the_window_is_dropped(self):
        cases = [days_ago(d) for d in (99, 96, 93)]
        self.assertEqual(problems.build_report(cases, NOW)["repeat_contact"], [])

    def test_placeholder_unit_is_kept_and_tagged(self):
        cases = [days_ago(d, unit="000") for d in (3, 2, 1)]
        cases += [days_ago(d, unit="204") for d in (3, 2, 1)]
        found = {c["unit"]: c for c in problems.build_report(cases, NOW)["repeat_contact"]}
        self.assertTrue(found["000"]["placeholderUnit"])
        self.assertFalse(found["204"]["placeholderUnit"])

    def test_placeholder_unit_names(self):
        for name in (None, "", "  ", "0", "000", "Undefined", "UNKNOWN", "N/A", "na", "none", "TBD", "--"):
            self.assertTrue(problems.is_placeholder_unit(name), repr(name))
        for name in ("101", "0101", "A", "2-101", "PH1", "10", "Office"):
            self.assertFalse(problems.is_placeholder_unit(name), repr(name))

    def test_min_repeats(self):
        cases = [days_ago(d) for d in (2, 1)]
        self.assertEqual(problems.build_report(cases, NOW)["repeat_contact"], [])
        self.assertEqual(len(problems.build_report(cases, NOW, min_repeats=2)["repeat_contact"]), 1)


class Burst(unittest.TestCase):
    # 28-day window from NOW starts Wed Feb 18, so the judged weeks are
    # Feb 23, Mar 2, and Mar 9. Mar 16 is in progress.
    KW = dict(window_days=28, baseline_weeks=4, min_cases=99, min_repeats=99)
    HISTORY = problems.history_start_for(NOW, 28, 4)
    BASELINE_WEEKS = [monday(1, 19), monday(1, 26), monday(2, 2), monday(2, 9), monday(2, 16)]

    def run_report(self, cases, **kw):
        return problems.build_report(cases, NOW, history_start=self.HISTORY, **{**self.KW, **kw})["burst"]

    def steady(self, per_week=2, weeks=None, **kw):
        out = []
        for week in weeks or self.BASELINE_WEEKS + [monday(2, 23), monday(3, 2), monday(3, 9)]:
            out += in_week(week, per_week, **kw)
        return out

    def test_history_start(self):
        self.assertEqual(self.HISTORY, datetime(2026, 1, 19, tzinfo=ET))

    def test_steady_pair_is_quiet(self):
        self.assertEqual(self.run_report(self.steady()), [])

    def test_burst_week_is_flagged(self):
        cases = self.steady() + in_week(monday(3, 2), 8, account="A2")
        found = self.run_report(cases)
        self.assertEqual(len(found), 1)
        c = found[0]
        self.assertEqual(c["weeks"], [{"weekOf": "2026-03-02", "count": 10, "baseline": 2.0, "ratio": 5.0}])
        self.assertEqual((c["count"], c["peakExcess"], c["propertyCount"]), (10, 8.0, 2))
        self.assertEqual([p["count"] for p in c["properties"]], [8, 2])

    def test_one_spike_does_not_raise_the_next_weeks_baseline(self):
        cases = self.steady() + in_week(monday(3, 2), 8) + in_week(monday(3, 9), 5)
        weeks = self.run_report(cases)[0]["weeks"]
        self.assertEqual([w["weekOf"] for w in weeks], ["2026-03-02", "2026-03-09"])
        self.assertEqual(weeks[1], {"weekOf": "2026-03-09", "count": 7, "baseline": 2.0, "ratio": 3.5})

    def test_ratio_met_but_excess_too_small(self):
        # 2 a week, then 6: 3x the baseline but only 4 over it.
        cases = self.steady() + in_week(monday(3, 2), 4)
        self.assertEqual(self.run_report(cases), [])
        self.assertEqual(len(self.run_report(cases, burst_min_excess=4)), 1)

    def test_excess_met_but_ratio_too_small(self):
        # 10 a week, then 16: 6 over, but only 1.6x.
        cases = self.steady(per_week=10) + in_week(monday(3, 2), 6)
        self.assertEqual(self.run_report(cases), [])
        self.assertEqual(len(self.run_report(cases, burst_ratio=1.5)), 1)

    def test_new_pair_with_no_baseline(self):
        found = self.run_report(in_week(monday(3, 9), 5, product="Portal", type_="Alerts"))
        self.assertEqual(found[0]["weeks"], [{"weekOf": "2026-03-09", "count": 5, "baseline": 0.0, "ratio": None}])

    def test_week_in_progress_is_not_judged(self):
        start = datetime(2026, 3, 16, 8, 0, tzinfo=ET)
        cases = self.steady() + [case(start + timedelta(hours=i), unit=f"p{i}") for i in range(20)]
        self.assertEqual(self.run_report(cases), [])

    def test_week_straddling_the_window_start_is_not_judged(self):
        # Feb 16's week began before the window did.
        cases = self.steady() + in_week(monday(2, 16), 20)
        self.assertEqual([w["weekOf"] for c in self.run_report(cases) for w in c["weeks"]], [])

    def test_short_history_is_not_read_as_quiet_weeks(self):
        cases = in_week(monday(3, 9), 6)
        late_start = datetime(2026, 2, 16, tzinfo=ET)  # only 3 whole weeks before Mar 9
        found = problems.build_report(cases, NOW, history_start=late_start, **self.KW)["burst"]
        self.assertEqual(found, [])
        self.assertEqual(len(self.run_report(cases)), 1)

    def test_default_history_start_is_the_earliest_case(self):
        cases = self.steady() + in_week(monday(3, 2), 8)
        # Earliest case is Mon Jan 19 at noon, so that week isn't whole and
        # Feb 23 (which needs it) can't be judged; Mar 2 still can.
        found = problems.build_report(cases, NOW, **self.KW)["burst"]
        self.assertEqual([w["weekOf"] for w in found[0]["weeks"]], ["2026-03-02"])

    def test_weeks_are_local_not_utc(self):
        # Sunday 11:30 PM Eastern is already Monday in UTC. It belongs to
        # the week that's ending, not the one about to start.
        late_sunday = datetime(2026, 3, 8, 23, 30, tzinfo=ET)
        cases = self.steady(weeks=self.BASELINE_WEEKS + [monday(2, 23)])
        cases += [case(late_sunday.astimezone(timezone.utc), unit=f"s{i}") for i in range(7)]
        weeks = self.run_report(cases)[0]["weeks"]
        self.assertEqual([(w["weekOf"], w["count"]) for w in weeks], [("2026-03-02", 7)])

    def test_cases_with_no_property_are_not_counted_as_one(self):
        cases = in_week(monday(3, 9), 6, account=None) + in_week(monday(3, 9), 1, account="A7")
        c = self.run_report(cases)[0]
        self.assertEqual((c["count"], c["propertyCount"]), (7, 1))
        self.assertEqual(c["properties"][0], {"name": "(no property)", "parent": None, "count": 6})

    def test_unclassified_cannot_burst(self):
        cases = in_week(monday(3, 9), 30, product="Undefined", type_="Not Enough Information")
        self.assertEqual(self.run_report(cases), [])

    def test_baseline_only_cases_stay_out_of_the_window_counts(self):
        report = problems.build_report(self.steady(), NOW, history_start=self.HISTORY, **self.KW)
        in_window = [c for c in self.steady() if c["created"] >= NOW - timedelta(days=28)]
        self.assertEqual(report["coverage"]["cases"], len(in_window))


class Drift(unittest.TestCase):
    # The week in progress starts Mon Mar 16, so the recent block is the
    # weeks of Feb 16 to Mar 9 (the spring DST change falls inside it) and
    # the baseline block is 13 weeks earlier: Nov 17 to Dec 8.
    BASELINE = date(2025, 11, 17)
    GAP = date(2025, 12, 15)
    RECENT = date(2026, 2, 16)
    YEAR = timedelta(weeks=52)
    HISTORY = problems.drift_history_start_for(NOW)
    KW = dict(min_cases=999, min_repeats=999)

    def weeks(self, first, counts, **kw):
        out = []
        for i, n in enumerate(counts):
            out += in_week(first + timedelta(weeks=i), n, **kw)
        return out

    def pair(self, baseline, recent, gap=None, **kw):
        """A pair with `baseline` and `recent` cases a week in its two
        blocks and `gap` a week (default: same as baseline) in between."""
        gap = baseline if gap is None else gap
        return (
            self.weeks(self.BASELINE, [baseline] * 4, **kw)
            + self.weeks(self.GAP, [gap] * 9, **kw)
            + self.weeks(self.RECENT, [recent] * 4, **kw)
        )

    def run_report(self, cases, history=None, **kw):
        return problems.build_report(
            cases, NOW, history_start=history or self.HISTORY, **{**self.KW, **kw}
        )

    def drift(self, cases, **kw):
        return self.run_report(cases, **kw)["drift"]

    def test_history_start_reaches_the_year_ago_baseline(self):
        self.assertEqual(self.HISTORY, datetime(2024, 11, 18, tzinfo=ET))

    def test_blocks_are_reported(self):
        blocks = self.run_report([])["driftBlocks"]
        self.assertEqual(blocks, {
            "recentWeeks": ["2026-02-16", "2026-02-23", "2026-03-02", "2026-03-09"],
            "baselineWeeks": ["2025-11-17", "2025-11-24", "2025-12-01", "2025-12-08"],
            "judged": True,
            "yearAgoCovered": True,
        })

    def test_steady_pair_is_quiet(self):
        self.assertEqual(self.drift(self.pair(10, 10)), [])

    def test_slow_ramp_is_flagged_where_burst_stays_silent(self):
        ramp = [10, 10, 10, 10, 11, 12, 12, 13, 14, 14, 15, 16, 17, 17, 18, 19, 20]
        report = self.run_report(self.weeks(self.BASELINE, ramp))
        self.assertEqual(report["burst"], [])
        self.assertEqual(len(report["drift"]), 1)
        c = report["drift"][0]
        self.assertEqual((c["product"], c["type"], c["weakType"]), ("Lock", "Keypad Issue", False))
        self.assertEqual((c["recentCount"], c["baselineCount"], c["ratio"], c["excess"]), (74, 40, 1.9, 34))
        self.assertEqual([w["count"] for w in c["weekly"]], ramp)
        self.assertEqual(
            [w["block"] for w in c["weekly"]], ["baseline"] * 4 + [None] * 9 + ["recent"] * 4
        )
        self.assertEqual((c["weekly"][0]["weekOf"], c["weekly"][-1]["weekOf"]), ("2025-11-17", "2026-03-09"))

    def test_candidate_cases_are_the_recent_block(self):
        c = self.drift(self.pair(5, 12, account="A1") + self.weeks(self.RECENT, [3] * 4, account="A2"))[0]
        self.assertEqual((c["count"], c["recentCount"], len(c["cases"])), (60, 60, 60))
        self.assertEqual([p["count"] for p in c["properties"]], [48, 12])
        first = datetime(2026, 2, 16, tzinfo=ET).isoformat()
        self.assertTrue(all(row["created"] >= first for row in c["cases"]))

    def test_ratio_met_but_excess_too_small(self):
        # 5 a week, then 9: 1.8x, but only 16 over across the block.
        cases = self.pair(5, 9)
        self.assertEqual(self.drift(cases), [])
        self.assertEqual(len(self.drift(cases, drift_min_excess=16)), 1)

    def test_excess_met_but_ratio_too_small(self):
        # 25 a week, then 32: 28 over, but only 1.28x.
        cases = self.pair(25, 32)
        self.assertEqual(self.drift(cases), [])
        self.assertEqual(len(self.drift(cases, drift_ratio=1.25)), 1)

    def test_thresholds_are_inclusive(self):
        # 10 a week, then 15: exactly 1.5x and exactly 20 over.
        self.assertEqual(len(self.drift(self.pair(10, 15))), 1)
        # 1.1 * 100 is 110.00000000000001 in floating point.
        self.assertEqual(len(self.drift(self.pair(25, 27, gap=0) + self.weeks(self.RECENT, [2]),
                                        drift_ratio=1.1, drift_min_excess=10)), 1)

    def test_new_pair_with_nothing_a_quarter_ago(self):
        c = self.drift(self.weeks(self.RECENT, [5] * 4, product="Portal", type_="Alerts"))[0]
        self.assertEqual((c["recentCount"], c["baselineCount"], c["ratio"]), (20, 0, None))

    def test_week_in_progress_is_not_counted(self):
        start = datetime(2026, 3, 16, 8, 0, tzinfo=ET)
        cases = self.pair(10, 10) + [case(start + timedelta(hours=i), unit=f"p{i}") for i in range(40)]
        self.assertEqual(self.drift(cases), [])

    def test_weeks_between_the_blocks_do_not_count(self):
        self.assertEqual(self.drift(self.pair(10, 10, gap=40)), [])
        # ...and the week just before the baseline block doesn't either.
        early = in_week(self.BASELINE - timedelta(weeks=1), 60)
        self.assertEqual(len(self.drift(self.pair(10, 16) + early)), 1)

    def test_pairs_are_judged_separately(self):
        cases = self.pair(6, 8) + self.pair(6, 8, type_="Battery") + self.pair(6, 8, product="Hub")
        self.assertEqual(self.drift(cases), [])

    def test_weeks_are_local_not_utc(self):
        # Sunday 11:30 PM Eastern closes the recent block; half an hour
        # into Monday is the week in progress.
        cases = self.pair(5, 5)
        late_sunday = datetime(2026, 3, 15, 23, 30, tzinfo=ET).astimezone(timezone.utc)
        cases += [case(late_sunday, unit=f"s{i}") for i in range(19)]
        monday_morning = datetime(2026, 3, 16, 0, 30, tzinfo=ET).astimezone(timezone.utc)
        self.assertEqual(self.drift(cases + [case(monday_morning, unit="m")]), [])
        found = self.drift(cases + [case(late_sunday, unit="s19")])
        self.assertEqual((found[0]["recentCount"], found[0]["weekly"][-1]["count"]), (40, 25))

    def test_as_of_a_sunday_midnight_counts_the_week_just_closed(self):
        as_of = datetime(2026, 3, 16, tzinfo=ET)
        self.assertEqual(len(problems.build_report(
            self.pair(10, 15), as_of, history_start=self.HISTORY, **self.KW
        )["drift"]), 1)

    def test_week_in_progress_is_local_on_the_now_side_too(self):
        # Sunday 10 PM Eastern is already Monday in UTC. The week of Mar 9
        # is still in progress, so the recent block ends with Mar 2.
        as_of = datetime(2026, 3, 15, 22, 0, tzinfo=ET).astimezone(timezone.utc)
        blocks = problems.build_report([], as_of, history_start=self.HISTORY)["driftBlocks"]
        self.assertEqual(blocks["recentWeeks"][-1], "2026-03-02")

    def test_short_history_is_not_read_as_a_quiet_baseline(self):
        cases = self.weeks(self.RECENT, [10] * 4)
        late = datetime(2025, 11, 18, tzinfo=ET)  # a day into the baseline block
        report = self.run_report(cases, history=late)
        self.assertEqual(report["drift"], [])
        self.assertEqual(
            (report["driftBlocks"]["judged"], report["driftBlocks"]["yearAgoCovered"]), (False, False)
        )
        self.assertEqual(len(self.drift(cases, history=datetime(2025, 11, 17, tzinfo=ET))), 1)

    def test_default_history_start_is_the_earliest_case(self):
        # Earliest case is noon on the baseline block's first Monday, so
        # that week isn't whole and nothing is judged.
        self.assertEqual(problems.build_report(self.pair(10, 20), NOW, **self.KW)["drift"], [])

    def test_year_ago_same_rise_is_tagged_not_removed(self):
        last_year = (
            self.weeks(self.BASELINE - self.YEAR, [8] * 4)
            + self.weeks(self.RECENT - self.YEAR, [14] * 4)
        )
        c = self.drift(self.pair(10, 18) + last_year)[0]
        self.assertEqual(
            c["yearAgo"], {"recentCount": 56, "baselineCount": 32, "ratio": 1.8, "drifted": True}
        )

    def test_year_ago_flat_is_not_seasonal(self):
        last_year = (
            self.weeks(self.BASELINE - self.YEAR, [10] * 4)
            + self.weeks(self.RECENT - self.YEAR, [11] * 4)
        )
        c = self.drift(self.pair(10, 18) + last_year)[0]
        self.assertEqual(
            c["yearAgo"], {"recentCount": 44, "baselineCount": 40, "ratio": 1.1, "drifted": False}
        )

    def test_year_ago_with_no_cases_then(self):
        c = self.drift(self.pair(10, 18))[0]
        self.assertEqual(
            c["yearAgo"], {"recentCount": 0, "baselineCount": 0, "ratio": None, "drifted": False}
        )

    def test_year_ago_not_covered_by_history_is_none(self):
        # One day short of the year-ago baseline block.
        late = self.HISTORY + timedelta(days=1)
        report = self.run_report(self.pair(10, 18), history=late)
        self.assertIsNone(report["drift"][0]["yearAgo"])
        self.assertEqual(
            (report["driftBlocks"]["judged"], report["driftBlocks"]["yearAgoCovered"]), (True, False)
        )

    def test_unclassified_cannot_drift(self):
        cases = self.weeks(self.RECENT, [30] * 4, product="Undefined", type_="Not Enough Information")
        cases += self.weeks(self.RECENT, [30] * 4, product=None, type_=None)
        self.assertEqual(self.drift(cases), [])

    def test_weak_type_is_tagged(self):
        self.assertTrue(self.drift(self.pair(10, 18, type_="Other"))[0]["weakType"])

    def test_sorted_by_excess(self):
        cases = self.pair(10, 16) + self.pair(10, 30, type_="Battery") + self.pair(0, 6, product="Hub")
        self.assertEqual([c["excess"] for c in self.drift(cases)], [80, 24, 24])
        self.assertEqual([c["product"] for c in self.drift(cases)], ["Lock", "Hub", "Lock"])

    def test_does_not_move_with_the_window(self):
        cases = self.pair(10, 18)
        self.assertEqual(self.drift(cases, window_days=7), self.drift(cases, window_days=90))

    def test_longer_history_does_not_lengthen_repeat_contact_chains(self):
        # One unit calling every 10 days for 400 days. The chain is only
        # followed back as far as the burst baseline reaches, however much
        # history was fetched for drift.
        cases = [days_ago(d) for d in range(5, 400, 10)]
        chain_start = problems.history_start_for(NOW, 90, 8)
        kept = [c for c in cases if c["created"] >= chain_start]
        self.assertLess(len(kept), len(cases))
        found = problems.build_report(cases, NOW, history_start=self.HISTORY)["repeat_contact"]
        self.assertEqual([c["count"] for c in found], [len(kept)])


class Page(unittest.TestCase):
    def render(self, cases, **kw):
        report = problems.build_report(cases, NOW, history_start=Drift.HISTORY, **kw)
        return problem_page.render({"generatedAt": NOW.isoformat(), "instanceUrl": "https://x", **report})

    def test_drift_section(self):
        d = Drift()
        last_year = d.weeks(d.BASELINE - d.YEAR, [8] * 4) + d.weeks(d.RECENT - d.YEAR, [14] * 4)
        page = self.render(
            d.pair(10, 18) + last_year + d.weeks(d.RECENT, [5] * 4, product="Portal", type_="Alerts")
        )
        self.assertIn("Drifting above a quarter ago (2)", page)
        self.assertIn("(Feb 16 to Mar 15)", page)
        self.assertIn("(Nov 17 to Dec 14)", page)
        self.assertIn("72 vs 40 a quarter earlier (1.8x)", page)
        self.assertIn("20 vs 0 a quarter earlier (from nothing)", page)
        self.assertIn("Same weeks a year earlier: 56 vs 32 (1.8x). It would have flagged then too", page)
        self.assertIn("Same weeks a year earlier: 0 vs 0. It wouldn't have flagged then.", page)
        self.assertEqual(page.count("also rose a year ago</span>"), 1)

    def test_drift_without_year_ago_history(self):
        d = Drift()
        report = problems.build_report(
            d.pair(10, 18), NOW, history_start=datetime(2025, 11, 17, tzinfo=ET)
        )
        page = problem_page.render({"generatedAt": NOW.isoformat(), "instanceUrl": "https://x", **report})
        self.assertIn("no year-ago history</span>", page)

    def test_drift_not_judged_says_so(self):
        report = problems.build_report([days_ago(1)], NOW)
        page = problem_page.render({"generatedAt": NOW.isoformat(), "instanceUrl": "https://x", **report})
        self.assertIn("Not judged: the case history", page)

    def test_every_section_renders(self):
        cases = [days_ago(d) for d in (30, 20, 10, 5, 1)]
        page = self.render(cases, min_cases=3)
        for label in ("Recurring at one property (1)", "Repeat contacts from one unit (1)",
                      "Bursts above", "Drifting above a quarter ago (0)", "Pairs drifting up"):
            self.assertIn(label, page)


class Determinism(unittest.TestCase):
    def test_input_order_does_not_matter(self):
        rng = random.Random(7)
        cases = []
        for i in range(400):
            cases.append(case(
                NOW - timedelta(days=rng.uniform(0, 140)),
                product=rng.choice(["Lock", "Hub", "Undefined", None]),
                type_=rng.choice(["Battery", "Offline", "Other", None]),
                account=rng.choice(["A1", "A2", "A3", None]),
                unit=rng.choice([f"U{n}" for n in range(12)] + [None]),
                closed=rng.random() < 0.8,
            ))
        history = problems.history_start_for(NOW, 90, 8)
        first = problems.build_report(cases, NOW, history_start=history, min_cases=3)
        self.assertTrue(first["recurrence"] and first["repeat_contact"])
        for seed in (1, 2, 3):
            shuffled = cases[:]
            random.Random(seed).shuffle(shuffled)
            again = problems.build_report(shuffled, NOW, history_start=history, min_cases=3)
            self.assertEqual(again, first)

    def test_every_candidate_case_is_classified_and_in_scope(self):
        rng = random.Random(11)
        cases = [
            case(NOW - timedelta(days=rng.uniform(0, 140)),
                 product=rng.choice(["Lock", "Undefined"]), type_="Battery",
                 account="A1", unit=rng.choice(["U1", "U2"]))
            for _ in range(200)
        ]
        by_number = {c["caseNumber"]: c for c in cases}
        report = problems.build_report(cases, NOW, min_cases=3)
        for signal in ("recurrence", "repeat_contact", "burst", "drift"):
            for candidate in report[signal]:
                self.assertEqual(candidate["count"], len(candidate["cases"]))
                for row in candidate["cases"]:
                    source = by_number[row["caseNumber"]]
                    self.assertEqual(source["product"], "Lock")
                if signal == "recurrence":
                    self.assertTrue(all(
                        by_number[row["caseNumber"]]["created"] >= NOW - timedelta(days=90)
                        for row in candidate["cases"]
                    ))


class Thresholds(unittest.TestCase):
    def test_rejects_nonsense(self):
        for bad in ({"window_days": 0}, {"min_cases": 0}, {"burst_min_excess": 0},
                    {"burst_ratio": 0.5}, {"repeat_days": -1}, {"baseline_weeks": 0},
                    {"drift_weeks": 0}, {"drift_min_excess": 0}, {"drift_ratio": 0.9},
                    {"drift_offset_weeks": 3}, {"drift_offset_weeks": 49}):
            with self.assertRaises(ValueError, msg=bad):
                problems.build_report([], NOW, **bad)


if __name__ == "__main__":
    unittest.main()
