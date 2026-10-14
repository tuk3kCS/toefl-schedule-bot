import tempfile
import unittest
from pathlib import Path

from bot import Config, latest_schedule, load_state, save_state, schedule_fingerprint


class BotTests(unittest.TestCase):
    def test_id_is_the_schedule_identity(self) -> None:
        first = {"id": "abc", "dateTest": "2026-11-01T00:00:00Z", "isOpen": True}
        changed = {"id": "abc", "dateTest": "2026-11-02T00:00:00Z", "isOpen": False}
        self.assertEqual(schedule_fingerprint(first), schedule_fingerprint(changed))

    def test_fallback_fingerprint_is_stable(self) -> None:
        schedule = {
            "examName": "TOEFL ITP - trên máy tính",
            "dateTest": "2026-11-01T00:00:00Z",
            "timeTest": "08:45 - 11:45",
            "area": "Hà Nội",
            "headQuarter": "Văn phòng Trung Yên - HN",
        }
        self.assertEqual(schedule_fingerprint(schedule), schedule_fingerprint(dict(reversed(schedule.items()))))

    def test_latest_schedule_uses_date_then_time(self) -> None:
        schedules = [
            {"id": "a", "dateTest": "2026-11-01T00:00:00Z", "timeTest": "13:45 - 16:45"},
            {"id": "b", "dateTest": "2026-11-02T00:00:00Z", "timeTest": "08:45 - 11:45"},
        ]
        self.assertEqual(latest_schedule(schedules)["id"], "b")

    def test_state_contains_only_filter_and_fingerprints(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            path = Path(directory) / "state.json"
            save_state(path, config=Config(), fingerprints=["a", "b"])
            state = load_state(path)
            self.assertEqual(state["seenFingerprints"], ["a", "b"])
            self.assertNotIn("schedules", state)


if __name__ == "__main__":
    unittest.main()
