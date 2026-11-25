"""周报调度测试：到期判定、幂等、空周退回。"""

import io
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cuoti  # noqa: E402


def run(argv):
    out = io.StringIO()
    with redirect_stdout(out):
        code = cuoti.main(argv)
    return code, out.getvalue()


class DueWeeksTest(unittest.TestCase):
    def test_nothing_due_before_sunday_evening(self):
        current = date(2026, 11, 16)
        self.assertEqual(cuoti.due_weeks(None, current, datetime(2026, 11, 21, 21, 0), max_weeks=1), [])

    def test_current_week_due_after_sunday_2000(self):
        current = date(2026, 11, 16)
        due = cuoti.due_weeks(None, current, datetime(2026, 11, 22, 20, 30), max_weeks=1)
        self.assertEqual(due, [current])

    def test_already_reported_week_is_not_repeated(self):
        current = date(2026, 11, 16)
        due = cuoti.due_weeks(current, current, datetime(2026, 11, 23, 12, 0), max_weeks=8)
        self.assertEqual(due, [])

    def test_backfill_after_missed_weeks(self):
        last = cuoti.week_monday(date(2026, 11, 2))
        current = cuoti.week_monday(date(2026, 11, 23))
        due = cuoti.due_weeks(last, current, datetime(2026, 11, 23, 12, 0))
        self.assertEqual(due, [date(2026, 11, 9), date(2026, 11, 16)])


class WeeklyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cuoti-weekly-"))
        cuoti.set_paths(self.tmp / "data", self.tmp / "reports", self.tmp / "logs")
        data = cuoti.load()
        cuoti.upsert(data, {"problem": "题一", "points": ["极限"], "first_seen": "2026-11-17"})
        cuoti.save(data)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_weekly_is_idempotent(self):
        code, out = run(["weekly"])
        self.assertEqual(code, 0)
        made = list(cuoti.REPORT_DIR.glob("math-*.html"))
        self.assertTrue(made, "第一次 weekly 应该出报告")
        code, out = run(["weekly"])
        self.assertIn("无需生成", out)
        self.assertEqual(len(list(cuoti.REPORT_DIR.glob("math-*.html"))), len(made))

    def test_report_falls_back_to_last_week_with_data(self):
        code, out = run(["report"])
        self.assertEqual(code, 0)
        self.assertIn("本周还没有数据", out)
        self.assertIn("math-2026-W47.html", out)

    def test_doctor_reports_counts(self):
        run(["report"])
        code, out = run(["doctor"])
        self.assertEqual(code, 0)
        self.assertIn("题量", out)
        self.assertIn("1 道", out)


if __name__ == "__main__":
    unittest.main()
