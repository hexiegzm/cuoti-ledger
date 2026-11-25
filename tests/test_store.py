"""存储层测试：空账本、BOM、备份、往返。"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cuoti  # noqa: E402


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cuoti-store-"))
        cuoti.set_paths(self.tmp / "data", self.tmp / "reports", self.tmp / "logs")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_empty_when_missing(self):
        self.assertFalse(cuoti.DATA_FILE.exists())
        data = cuoti.load()
        self.assertEqual(data["problems"], [])

    def test_roundtrip_is_utf8_and_readable(self):
        data = cuoti.load()
        cuoti.upsert(data, {"problem": "求 ∫₀¹ x dx", "points": ["定积分"]})
        cuoti.save(data)
        raw = cuoti.DATA_FILE.read_text(encoding="utf-8")
        self.assertIn("求 ∫₀¹ x dx", raw)          # 没被转成 \uXXXX
        again = cuoti.load()
        self.assertEqual(again["problems"][0]["problem"], "求 ∫₀¹ x dx")

    def test_bom_is_tolerated(self):
        cuoti.DATA_DIR.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "problems": [{"id": "20260101-001", "problem": "题",
                                               "points": ["极限"], "solved": False}]}
        cuoti.DATA_FILE.write_bytes(b"\xef\xbb\xbf" + json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        self.assertEqual(cuoti.load()["problems"][0]["id"], "20260101-001")

    def test_backup_before_overwrite(self):
        data = cuoti.load()
        cuoti.upsert(data, {"problem": "题一", "points": ["极限"]})
        cuoti.save(data)
        cuoti.upsert(data, {"problem": "题二", "points": ["极限"]})
        cuoti.save(data)
        backups = list(cuoti.BACKUP_DIR.glob("mistakes-*.json"))
        self.assertEqual(len(backups), 1)
        self.assertIn("题一", backups[0].read_text(encoding="utf-8"))
        self.assertNotIn("题二", backups[0].read_text(encoding="utf-8"))

    def test_missing_fields_get_defaults(self):
        cuoti.DATA_DIR.mkdir(parents=True, exist_ok=True)
        cuoti.DATA_FILE.write_text(json.dumps(
            {"version": 1, "problems": [{"id": "x", "problem": "题", "first_seen": "2026-11-20"}]},
            ensure_ascii=False), encoding="utf-8")
        p = cuoti.load()["problems"][0]
        self.assertEqual(p["points"], ["未分类"])
        self.assertEqual(p["topic"], "未分类")
        self.assertFalse(p["solved"])
        self.assertFalse(p["starred"])


if __name__ == "__main__":
    unittest.main()
