"""渲染层测试：六视图、遮挡、答案页、转义。"""

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cuoti  # noqa: E402

WEEK = date(2026, 11, 16)   # 2026-W47 周一


def build():
    data = {"version": 1, "problems": []}
    cuoti.upsert(data, {
        "problem": "求 lim(x→0) (tan x − sin x)/x³", "topic": "极限",
        "points": ["等价无穷小", "泰勒展开"], "kind": "方法",
        "my_work": "tan x ≈ x，分子 = 0", "stuck": "加减法里直接做等价替换",
        "answer": "1/2", "fix": "先通分再替换", "first_seen": "2026-11-18",
    })
    cuoti.upsert(data, {
        "problem": "求 lim(x→0) (1 − cos x)/x²", "topic": "极限",
        "points": ["等价无穷小"], "kind": "方法",
        "stuck": "没先想 x²/2", "answer": "1/2", "first_seen": "2026-11-19",
    })
    cuoti.upsert(data, {
        "problem": "求 ∫₀¹ x·√(1−x²) dx", "topic": "一元积分",
        "points": ["定积分换元"], "kind": "计算", "answer": "1/3",
        "first_seen": "2026-11-10",        # 上一周
    })
    return data


class RenderTest(unittest.TestCase):
    def test_six_panels_and_only_one_on(self):
        doc = cuoti.render_html(build(), WEEK)
        for key in ("study", "weak", "t1", "t2", "fav", "solved"):
            self.assertIn(f'data-panel="{key}"', doc, f"缺少 {key} 视图")
        self.assertEqual(doc.count('<section class="panel'), 6)
        self.assertEqual(doc.count('class="panel on"'), 1)

    def test_cards_not_tables(self):
        doc = cuoti.render_html(build(), WEEK)
        self.assertIn('class="card masked"', doc)
        self.assertNotIn("<table", doc)

    def test_blank_panels_have_answer_key(self):
        doc = cuoti.render_html(build(), WEEK)
        self.assertIn('data-blank="1"', doc)
        self.assertEqual(doc.count('class="answer-key'), 2)     # study + t2
        self.assertIn("答案 · 第", doc)

    def test_answer_hidden_in_masked_cards(self):
        doc = cuoti.render_html(build(), WEEK)
        card = doc.split('data-id="', 2)[1].split("</article>", 1)[0]
        self.assertIn('class="answer"', card)        # 藏在 .answer 里，点开才显示
        self.assertIn('class="tip"', card)
        self.assertIn('class="ansline"', card)
        self.assertIn(".card.masked .answer{display:none}", cuoti.CSS)

    def test_week_filter_in_t1(self):
        doc = cuoti.render_html(build(), WEEK)
        t1 = doc.split('id="p-t1"', 1)[1].split('id="p-t2"', 1)[0]
        self.assertIn("tan x", t1)
        self.assertNotIn("∫₀¹", t1)               # 上周的题不进表一

    def test_weak_view_repeats_first_and_chips_rest(self):
        doc = cuoti.render_html(build(), WEEK)
        weak = doc.split('id="p-weak"', 1)[1].split('id="p-t1"', 1)[0]
        self.assertIn("反复错的", weak)
        self.assertIn("只错过一次的", weak)
        self.assertIn('class="chip" data-q="泰勒展开"', weak)

    def test_search_placeholder_and_tabs(self):
        doc = cuoti.render_html(build(), WEEK)
        self.assertIn('id="q"', doc)
        for key in ("n-study", "n-weak", "n-t1", "n-t2", "n-fav", "n-solved"):
            self.assertIn(f'id="{key}"', doc)

    def test_interactive_mode_has_buttons(self):
        doc = cuoti.render_app(build(), WEEK, interactive=True)
        self.assertIn('data-act="star"', doc)
        self.assertIn('data-act="solved"', doc)
        self.assertIn("/api/mark", doc)
        self.assertIn("await refreshPanels();", doc)

    def test_static_mode_has_badges_not_buttons(self):
        data = build()
        data["problems"][0]["starred"] = True
        doc = cuoti.render_html(data, WEEK)
        self.assertIn('class="badge star"', doc)
        self.assertNotIn('data-act="star"', doc)

    def test_html_is_escaped(self):
        data = {"version": 1, "problems": []}
        cuoti.upsert(data, {"problem": "<script>alert(1)</script>", "points": ["<b>x</b>"],
                            "stuck": "<img src=x onerror=alert(1)>"})
        for interactive in (False, True):
            doc = cuoti.render_app(data, WEEK, interactive=interactive)
            self.assertNotIn("<script>alert(1)</script>", doc)
            self.assertNotIn("<img src=x", doc)

    def test_empty_ledger_renders(self):
        for interactive in (False, True):
            doc = cuoti.render_app({"problems": []}, WEEK, interactive=interactive)
            self.assertIn("<!DOCTYPE html>", doc)

    def test_week_ordinal_counts_from_first_problem(self):
        data = build()
        self.assertEqual(cuoti.week_ordinal(data, date(2026, 11, 9)), 1)
        self.assertEqual(cuoti.week_ordinal(data, date(2026, 11, 16)), 2)
        self.assertEqual(cuoti.week_ordinal(data, date(2026, 12, 7)), 5)

    def test_title_uses_ordinal_not_iso_week(self):
        doc = cuoti.render_html(build(), WEEK)
        self.assertIn("<h1>第 2 周 · 错题报告</h1>", doc)
        self.assertNotIn("第 47 周", doc)

    def test_print_css_hides_answers_only_in_blank_panels(self):
        self.assertIn('.panel[data-blank="1"] .answer{display:none}', cuoti.CSS)
        self.assertIn('.panel[data-blank="1"] .answer-key{display:block', cuoti.CSS)

    def test_solved_problem_leaves_open_panels(self):
        data = build()
        p = data["problems"][1]          # 1 − cos x
        p["solved"] = True
        p["solved_at"] = "2026-11-20"
        doc = cuoti.render_html(data, WEEK)
        t2 = doc.split('id="p-t2"', 1)[1].split('id="p-fav"', 1)[0]
        self.assertNotIn("1 − cos x", t2)
        done = doc.split('id="p-solved"', 1)[1]
        self.assertIn("1 − cos x", done)


if __name__ == "__main__":
    unittest.main()
