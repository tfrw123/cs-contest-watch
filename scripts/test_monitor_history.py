"""Exercise history using independent reads and writes across scheduled checks."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import monitor

USACO = monitor.SOURCES["USACO"][0]
NZIC = monitor.SOURCES["NZIC"][0]
SCHEDULE = "2026–2027 Schedule\nJan 8–11: First Contest\nMar 27: US Open\nOur Sponsors"
NZIC_NOTICE = (
    "Registration for NZIC 2027 is expected to open in February 2027.\n"
    "The contest schedule will be announced in early 2027. For reference, "
    "the schedule for NZIC 2026 was:\nMonday 23 March - Sunday 29 March"
)


class MonitorHistoryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        for field, name in [("DATA", "contests"), ("STATUS", "status"), ("HISTORY", "history")]:
            patcher = patch.object(monitor, field, root / f"{name}.json")
            patcher.start()
            self.addCleanup(patcher.stop)
        self.now = datetime(2026, 11, 10, tzinfo=timezone.utc)
        self.write(monitor.DATA, {"season": "2026–2027", "contests": [
            {"key": "USACO", "events": []}, {"key": "NZIC", "events": []}, {"key": "ACSL", "events": []},
        ]})
        self.pages = {USACO: SCHEDULE, NZIC: NZIC_NOTICE}
        self.changed_other = "Unrelated navigation"
        self.failure = None

    def write(self, path, value):
        path.write_text(json.dumps(value), encoding="utf-8")

    def read(self, path):
        return json.loads(path.read_text())

    def run_monitor(self, hours):
        def fetch(url):
            if self.failure == url:
                raise OSError("Connection failed")
            text = self.pages.get(url, self.changed_other)
            return {"url": url, "text": text, "sha256": monitor.hashlib.sha256(text.encode()).hexdigest()}

        with patch.object(monitor, "fetch", fetch), patch.dict(monitor.os.environ, {"GITHUB_EVENT_NAME": ""}):
            monitor.main(self.now + timedelta(hours=hours))
        return self.read(monitor.HISTORY)

    def test_repeated_checks_and_distinct_date_changes(self):
        initial = self.run_monitor(0)
        self.assertEqual(len(initial), 3)
        self.assertEqual(self.run_monitor(1), initial)
        self.changed_other = "New advertisement"
        self.assertEqual(self.run_monitor(12), initial)
        self.assertEqual(self.read(monitor.STATUS)["sources"]["ACSL"]["pages"][monitor.SOURCES["ACSL"][0]]["changeState"], "detected_change")
        self.pages[USACO] += "\nNews unrelated to schedule."
        self.assertEqual(self.run_monitor(24), initial)
        self.assertEqual(self.read(monitor.DATA)["latestUpdateIds"], [])
        self.pages[USACO] = SCHEDULE.replace("Jan 8–11: First Contest", "Jan 9–12: Contest 1")
        updated = self.run_monitor(36)
        self.assertEqual(len(updated), 4)
        self.assertEqual(updated[0]["changeType"], "date_adjusted")
        self.assertEqual(updated[0]["before"]["start"], "2027-01-08")
        self.assertEqual(updated[0]["after"]["start"], "2027-01-09")
        self.assertEqual(updated[0]["season"], "2026–2027")
        self.assertEqual(self.run_monitor(48), updated)
        self.assertTrue(all(item in updated for item in initial))

    def test_problems_stay_out_of_history_and_preserve_dates(self):
        initial = self.run_monitor(0)
        saved = self.read(monitor.DATA)["contests"][0]["events"]
        self.pages[USACO] = "2026–2027 Schedule\nDates are stored in an unrecognized widget"
        self.assertEqual(self.run_monitor(12), initial)
        self.assertEqual(self.read(monitor.DATA)["monitorIssues"][0]["type"], "parse_error")
        self.pages[USACO] = SCHEDULE.replace("Jan 8–11", "Jan 11–8")
        self.assertEqual(self.run_monitor(24), initial)
        self.assertEqual(self.read(monitor.DATA)["monitorIssues"][0]["type"], "date_conflict")
        self.failure = USACO
        self.assertEqual(self.run_monitor(36), initial)
        result = self.read(monitor.DATA)
        self.assertEqual(result["monitorIssues"][0]["type"], "fetch_error")
        self.assertEqual(result["contests"][0]["events"], saved)

    def test_legacy_cleanup_preserves_valid_announcements_and_seasons(self):
        def announcement(year, detected):
            event = monitor.parse_usaco_schedule(SCHEDULE.replace("2026–2027", f"{year}–{year + 1}"), year)[0]
            return {"contest": "USACO", "type": "schedule_updated", "detectedAt": detected,
                    "url": USACO, "before": [], "after": [event]}
        older = announcement(2025, "2025-11-10T00:00:00Z")
        current = announcement(2026, "2026-11-10T00:00:00Z")
        duplicate = {**current, "detectedAt": "2026-11-10T12:00:00Z"}
        noise = {"contest": "USACO", "type": "official_page_changed"}
        history = monitor.clean_history([noise, duplicate, current, older])
        self.assertEqual([item["season"] for item in history], ["2026–2027", "2025–2026"])
        self.assertEqual(history[0]["detectedAt"], current["detectedAt"])
        self.assertEqual(monitor.clean_history(history), history)

    def test_nzic_historical_dates_are_not_verified(self):
        history = self.run_monitor(0)
        nzic = [item for item in history if item["contest"] == "NZIC"]
        self.assertEqual(len(nzic), 1)
        self.assertEqual(nzic[0]["changeType"], "estimate_updated")
        self.assertEqual(nzic[0]["after"]["month"], "2027-02")
        self.assertEqual(nzic[0]["after"]["status"], "expected")
        self.pages[NZIC] = (
            "NZIC 2027 Schedule\nMonday 22 March - Sunday 28 March\n"
            "Monday 28 June - Sunday 4 July\nMonday 16 August - Sunday 22 August"
        )
        updated = self.run_monitor(12)
        self.assertEqual(len(updated), len(history) + 3)
        self.assertEqual(self.run_monitor(24), updated)
        self.assertEqual(len(self.read(monitor.DATA)["contests"][1]["events"]), 4)
