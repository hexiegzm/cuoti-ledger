"""数据层测试：去重、只补空缺、定位、知识点统计。"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cuoti  # noqa: E402


def rec(text="求 lim(x→0) (tan x − sin x)/x³", **kw):
    base = {
        "problem": text,
        "topic": "极限",
        "points": ["等价无穷小"],
        "kind": "方法",
        "stuck": "加减法里直接做等价替换",
    }
    base.update(kw)
    return base


class UpsertTest(unittest.TestCase):
    def setUp(self):
        self.data = {"version": 1, "problems": []}

    def test_new_problem_defaults(self):
        p, created = cuoti.upsert(self.data, rec())
        self.assertTrue(created)
        self.assertFalse(p["solved"])
        self.assertIsNone(p["solved_at"])
        self.assertFalse(p["starred"])
        self.assertEqual(p["points"], ["等价无穷小"])

    def test_same_problem_not_duplicated(self):
        cuoti.upsert(self.data, rec())
        p, created = cuoti.upsert(self.data, rec("  求 lim(x→0) (tan x − sin x)/x³ "))
        self.assertFalse(created)
        self.assertEqual(len(self.data["problems"]), 1)
        self.assertEqual(p["id"], self.data["problems"][0]["id"])

    def test_merge_fills_only_blanks(self):
        p, _ = cuoti.upsert(self.data, rec())
        p["stuck"] = "我自己写的卡点"
        cuoti.upsert(self.data, rec(stuck="AI 的另一版", answer="1/2", fix="泰勒展开"))
        self.assertEqual(p["stuck"], "我自己写的卡点")     # 不覆盖
        self.assertEqual(p["answer"], "1/2")              # 补空缺
        self.assertEqual(p["fix"], "泰勒展开")

    def test_merge_unions_points_and_drops_uncategorized(self):
        p, _ = cuoti.upsert(self.data, {**rec(), "points": ["未分类"]})
        cuoti.upsert(self.data, rec(points=["等价无穷小", "泰勒展开"]))
        self.assertEqual(p["points"], ["等价无穷小", "泰勒展开"])

    def test_ids_are_unique_per_day(self):
        ids = [cuoti.upsert(self.data, rec(f"题 {i}"))[0]["id"] for i in range(3)]
        self.assertEqual(len(set(ids)), 3)
        self.assertTrue(all(i.startswith("2026") or i[:8].isdigit() for i in ids))


class ResolveTest(unittest.TestCase):
    def setUp(self):
        self.data = {"version": 1, "problems": []}
        cuoti.upsert(self.data, rec("求 ∫₀¹ x·√(1−x²) dx", points=["定积分换元"]))
        cuoti.upsert(self.data, rec("求 ∫∫_D (x²+y²) dxdy", points=["极坐标"]))

    def test_by_id(self):
        pid = self.data["problems"][0]["id"]
        p, err = cuoti.resolve(self.data, pid)
        self.assertIsNone(err)
        self.assertEqual(p["id"], pid)

    def test_by_unique_substring(self):
        p, err = cuoti.resolve(self.data, "tan")   # 不在库里
        self.assertIsNone(p)
        self.assertIn("没找到", err)
        p, err = cuoti.resolve(self.data, "√(1−x²)")
        self.assertIsNone(err)

    def test_ambiguous_is_an_error(self):
        p, err = cuoti.resolve(self.data, "求")
        self.assertIsNone(p)
        self.assertIn("命中 2 道题", err)
        self.assertIn("id", err)


class PointStatsTest(unittest.TestCase):
    def build(self):
        data = {"version": 1, "problems": []}
        cuoti.upsert(data, rec("题A", points=["等价无穷小"], first_seen="2026-11-18"))
        cuoti.upsert(data, rec("题B", points=["等价无穷小"], first_seen="2026-11-19"))
        cuoti.upsert(data, rec("题C", points=["等价无穷小", "泰勒展开"], first_seen="2026-11-21"))
        cuoti.upsert(data, rec("题D", points=["拉格朗日乘数法"], first_seen="2026-11-23"))
        return data

    def test_counts(self):
        st = cuoti.point_stats(self.build())
        self.assertEqual(st["等价无穷小"], {"total": 3, "open": 3, "last": "2026-11-21",
                                         "ids": st["等价无穷小"]["ids"]})
        self.assertEqual(len(st["等价无穷小"]["ids"]), 3)

    def test_weak_points_puts_repeated_first(self):
        weak = cuoti.weak_points(self.build())
        self.assertEqual(weak[0][0], "等价无穷小")
        self.assertGreater(weak[0][1]["open"], weak[1][1]["open"])

    def test_solved_leaves_weak_list(self):
        data = self.build()
        data["problems"][0]["solved"] = True          # 只剩 2 道
        st = cuoti.point_stats(data)
        self.assertEqual(st["等价无穷小"]["open"], 2)

    def test_sort_study_repeats_first(self):
        data = self.build()
        weak = {k: v["open"] for k, v in cuoti.point_stats(data).items()}
        order = [p["problem"] for p in cuoti.sort_study(data["problems"], weak)]
        self.assertEqual(order[0], "题C")             # 最新且在重复知识点上
        self.assertEqual(order[-1], "题D")            # 只有它一个知识点


if __name__ == "__main__":
    unittest.main()
