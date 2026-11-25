"""命令行测试：退出码、批量导入、LaTeX 提醒、定位失败。"""

import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cuoti  # noqa: E402


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cuoti.main(argv)
    return code, out.getvalue(), err.getvalue()


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cuoti-cli-"))
        cuoti.set_paths(self.tmp / "data", self.tmp / "reports", self.tmp / "logs")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_add_then_list(self):
        code, out, _ = run(["add", "求 lim(x→0) sin x / x", "--point", "等价无穷小"])
        self.assertEqual(code, 0)
        self.assertIn("记下 1 道新题", out)
        code, out, _ = run(["list"])
        self.assertEqual(code, 0)
        self.assertIn("sin x / x", out)
        self.assertIn("还没搞懂 1", out)

    def test_add_without_point_warns(self):
        code, out, _ = run(["add", "求 ∫ x dx"])
        self.assertEqual(code, 0)
        self.assertIn("没给知识点", out)

    def test_add_flags_latex(self):
        code, out, _ = run(["add", "求 \\frac{1}{2} 的值", "--point", "极限"])
        self.assertEqual(code, 0)
        self.assertIn("LaTeX", out)

    def test_file_import_is_idempotent(self):
        seed = self.tmp / "seed.json"
        seed.write_text(json.dumps({"problems": [
            {"problem": "题一", "points": ["极限"], "stuck": "卡在这里"},
        ]}, ensure_ascii=False), encoding="utf-8")
        code, out, _ = run(["add", "--file", str(seed)])
        self.assertEqual(code, 0)
        code, out, _ = run(["add", "--file", str(seed)])
        self.assertIn("合并 1 道已有的", out)
        self.assertEqual(len(cuoti.load()["problems"]), 1)

    def test_solve_moves_problem(self):
        run(["add", "求 lim sin x / x", "--point", "等价无穷小"])
        pid = cuoti.load()["problems"][0]["id"]
        code, out, _ = run(["solve", pid])
        self.assertEqual(code, 0)
        self.assertIn("搞懂了", out)
        p = cuoti.load()["problems"][0]
        self.assertTrue(p["solved"])
        self.assertIsNotNone(p["solved_at"])
        code, out, _ = run(["list"])
        self.assertIn("没有符合条件的题", out)

    def test_solve_missing_exits_nonzero(self):
        code, _, err = run(["solve", "20990101-001"])
        self.assertEqual(code, 1)
        self.assertIn("没找到", err)

    def test_solve_ambiguous_exits_nonzero(self):
        run(["add", "求 lim(x→0) sin x / x", "--point", "极限"])
        run(["add", "求 lim(x→∞) (1+1/x)^x", "--point", "极限"])
        code, _, err = run(["solve", "求 lim"])
        self.assertEqual(code, 1)
        self.assertIn("命中 2 道题", err)
        self.assertTrue(all(not p["solved"] for p in cuoti.load()["problems"]))

    def test_unsolve_keeps_stuck(self):
        run(["add", "求 lim sin x / x", "--point", "极限", "--stuck", "没想通"])
        pid = cuoti.load()["problems"][0]["id"]
        run(["solve", pid])
        run(["unsolve", pid])
        p = cuoti.load()["problems"][0]
        self.assertFalse(p["solved"])
        self.assertEqual(p["stuck"], "没想通")

    def test_star_and_unstar(self):
        run(["add", "题", "--point", "极限"])
        pid = cuoti.load()["problems"][0]["id"]
        run(["star", pid])
        self.assertTrue(cuoti.load()["problems"][0]["starred"])
        run(["unstar", pid])
        self.assertFalse(cuoti.load()["problems"][0]["starred"])

    def test_points_marks_repeats(self):
        for i in range(2):
            run(["add", f"题{i}", "--point", "换元换限"])
        code, out, _ = run(["points"])
        self.assertEqual(code, 0)
        self.assertIn("反复错", out)


if __name__ == "__main__":
    unittest.main()
