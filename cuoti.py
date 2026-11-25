#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cuoti-ledger · 本地错题账本（v1：只记不会的题，按知识点算账）
=============================================================

纯标准库、零依赖、零联网：把「这道题我卡在哪」记成一条记录，每周渲染成一张能打印的纸。

设计（详见 SPEC.md）：
  * 账本记的是【题】，统计和复习的单位是【知识点】——同一道题不会再考，同一个断点会；
  * 不算分、不排复习档期、不数「错了几次」；
  * 一道题只有两种状态：还没搞懂 / 搞懂了；
  * 唯一不可再生的资产是 `stuck`——具体断在哪一步。

目录::

    cuoti-ledger/
      cuoti.py                本脚本（唯一必需文件）
      data/mistakes.json      账本（唯一数据源，可直接手改）
      data/backup/            每次写入前的自动备份
      reports/                生成好的 HTML 周报
      logs/                   运行日志

常用命令::

    python cuoti.py add --file seed/week47.json
    python cuoti.py add "求 lim(x→0) (tan x - sin x)/x³" --topic 高数 \\
        --point 等价无穷小 --kind 方法 --answer "1/2" \\
        --stuck "在加减法里直接做等价替换，把三阶项丢了" \\
        --fix "加减法不能单独替换：通分，或泰勒展开到 x³" \\
        --source "2026-W47 660题 P42 T7"
    python cuoti.py solve 20261120-001        # 搞懂了（别名 learn）
    python cuoti.py unsolve 20261120-001      # 又不会了
    python cuoti.py points                    # 哪个知识点错了三次
    python cuoti.py app --open                # 遮挡着过一遍
    python cuoti.py report --open             # 出周报
    python cuoti.py weekly                    # 补漏 + 出当周（调度用）
    python cuoti.py doctor --task MathMistakeWeekly
"""

from __future__ import annotations

import argparse
import html
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time as _time
import unicodedata
from datetime import date, datetime, time, timedelta
from pathlib import Path

# ---------------------------------------------------------------- 路径与常量

BASE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("CUOTI_DATA") or (BASE / "data"))
DATA_FILE = DATA_DIR / "mistakes.json"
BACKUP_DIR = DATA_DIR / "backup"
REPORT_DIR = Path(os.environ.get("CUOTI_REPORTS") or (BASE / "reports"))
LOG_DIR = Path(os.environ.get("CUOTI_LOGS") or (BASE / "logs"))

SCHEMA_VERSION = 1
try:
    BUILD = datetime.fromtimestamp(Path(__file__).stat().st_mtime).strftime("%m-%d %H:%M")
except Exception:  # pragma: no cover
    BUILD = "?"
MAX_BACKUPS = 40
WEEKDAY = {0: "周一", 1: "周二", 2: "周三", 3: "周四", 4: "周五", 5: "周六", 6: "周日"}

KINDS = ("概念", "方法", "计算", "审题", "漏解")
KIND_ZH = {
    "概念": "定义/定理记错或不知道",
    "方法": "不知道从哪下手，或选了错的路",
    "计算": "会做，算错了",
    "审题": "看错条件、漏条件、答非所问",
    "漏解": "分类讨论不全、漏边界、漏重根",
}
# 模块的显示顺序即考研大纲顺序（高数 → 线代 → 概率）；不认识的模块排在后面，未分类永远最后
TOPIC_ORDER = [
    "高数", "极限", "一元微分", "一元积分", "多元微分", "重积分", "曲线曲面积分",
    "级数", "微分方程",
    "线代", "行列式", "矩阵", "向量组", "方程组", "特征值", "二次型",
    "概率", "随机变量", "分布", "数字特征", "大数定律", "参数估计", "假设检验",
]
STATE_ZH = {"open": "还没搞懂", "solved": "搞懂了"}


# ---------------------------------------------------------------- 小工具

def force_utf8() -> None:
    """Windows 控制台默认 GBK，统一成 UTF-8，避免 emoji / 中文报错。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def log(msg: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_DIR / "cuoti.log", "a", encoding="utf-8") as f:
        f.write(f"[{now_iso()}] {msg}\n")


def dw(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in s)


def pad(s: str, width: int) -> str:
    return s + " " * max(0, width - dw(s))


def cut(s: str, width: int) -> str:
    if dw(s) <= width:
        return s
    out, acc = [], 0
    for ch in s:
        w = 2 if unicodedata.east_asian_width(ch) in "WF" else 1
        if acc + w > width - 1:   # 留一格给省略号，保证总宽不超
            break
        out.append(ch)
        acc += w
    return "".join(out) + "…"


def parse_date(s: str | None) -> date:
    if not s:
        return date.today()
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise SystemExit(f"日期格式应为 YYYY-MM-DD，收到：{s}")


def week_key(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def week_monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def parse_week(s: str | None) -> date:
    """接受 2026-W47 或 2026-11-20，返回那周的周一。"""
    if not s:
        return week_monday(date.today())
    m = re.fullmatch(r"(\d{4})-?[Ww](\d{1,2})", s.strip())
    if m:
        try:
            return week_monday(date.fromisocalendar(int(m.group(1)), int(m.group(2)), 1))
        except ValueError:
            raise SystemExit(f"周格式不对：{s}（应为 2026-W47）")
    return week_monday(parse_date(s))


def in_range(iso: str | None, start: date, end: date) -> bool:
    if not iso:
        return False
    try:
        d = date.fromisoformat(iso)
    except ValueError:
        return False
    return start <= d <= end


def read_json(path: Path):
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):   # 记事本存的 UTF-8 BOM
        raw = raw[3:]
    return json.loads(raw.decode("utf-8") or "{}")


def set_paths(data_dir=None, report_dir=None, log_dir=None) -> None:
    """换账本/产物位置（测试、或同时管几本账时用）。不传的保持不变。"""
    global DATA_DIR, DATA_FILE, BACKUP_DIR, REPORT_DIR, LOG_DIR
    if data_dir:
        DATA_DIR = Path(data_dir)
        DATA_FILE = DATA_DIR / "mistakes.json"
        BACKUP_DIR = DATA_DIR / "backup"
    if report_dir:
        REPORT_DIR = Path(report_dir)
    if log_dir:
        LOG_DIR = Path(log_dir)


def one_line(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


# ---------------------------------------------------------------- 数据读写

def _blank() -> dict:
    return {"version": SCHEMA_VERSION, "updated_at": now_iso(), "problems": []}


def migrate(data: dict) -> dict:
    """补齐缺省字段，不动已有内容（SPEC §12.2）。"""
    if not isinstance(data, dict):
        raise SystemExit("账本格式不对：顶层应该是一个 JSON 对象")
    data.setdefault("version", SCHEMA_VERSION)
    data.setdefault("problems", [])
    for p in data["problems"]:
        if not p.get("points"):
            p["points"] = ["未分类"]
        if not p.get("topic"):
            p["topic"] = "未分类"
        p.setdefault("kind", "")
        p.setdefault("my_work", "")
        p.setdefault("stuck", "")
        p.setdefault("answer", "")
        p.setdefault("fix", "")
        p.setdefault("source", "")
        p["solved"] = bool(p.get("solved"))
        p.setdefault("solved_at", None)
        p["starred"] = bool(p.get("starred"))
        if not p.get("first_seen"):
            p["first_seen"] = date.today().isoformat()
        if not p.get("id"):
            p["id"] = _next_id(data, parse_date(p["first_seen"]))
    return data


def load() -> dict:
    if not DATA_FILE.exists():
        return _blank()
    return migrate(read_json(DATA_FILE))


def save(data: dict, backup: bool = True) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if backup and DATA_FILE.exists():
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = BACKUP_DIR / f"mistakes-{stamp}.json"
        n = 1
        while target.exists():
            target = BACKUP_DIR / f"mistakes-{stamp}-{n}.json"
            n += 1
        shutil.copy2(DATA_FILE, target)
        for old in sorted(BACKUP_DIR.glob("mistakes-*.json"))[:-MAX_BACKUPS]:
            try:
                old.unlink()
            except OSError:
                pass
    data["version"] = SCHEMA_VERSION
    data["updated_at"] = now_iso()
    tmp = DATA_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, DATA_FILE)


def _next_id(data: dict, d: date) -> str:
    prefix = d.strftime("%Y%m%d")
    used = {p.get("id") for p in data["problems"]}
    n = 1
    while f"{prefix}-{n:03d}" in used:
        n += 1
    return f"{prefix}-{n:03d}"


def find_problem(data: dict, key: str) -> dict | None:
    for p in data["problems"]:
        if p.get("id") == key:
            return p
    return None


def resolve(data: dict, key: str) -> tuple[dict | None, str | None]:
    """id 精确匹配优先；否则题干子串。命中多道是错误，不允许猜。"""
    key = (key or "").strip()
    if not key:
        return None, "空的关键字"
    hit = find_problem(data, key)
    if hit is not None:
        return hit, None
    kk = key.casefold()
    hits = [p for p in data["problems"] if kk in one_line(p.get("problem")).casefold()]
    if len(hits) == 1:
        return hits[0], None
    if not hits:
        return None, f"没找到「{key}」，用 list 看看 id"
    ids = "、".join(p["id"] for p in hits[:6])
    more = " 等" if len(hits) > 6 else ""
    return None, f"「{key}」命中 {len(hits)} 道题（{ids}{more}），请用 id 指定"


def upsert(data: dict, rec: dict) -> tuple[dict, bool]:
    """同一道题（题干相同）不重复记；只补空缺，不覆盖已有内容。"""
    text = one_line(rec.get("problem"))
    if not text:
        raise SystemExit("题干不能为空")
    for p in data["problems"]:
        if one_line(p.get("problem")).casefold() == text.casefold():
            _merge(p, rec)
            return p, False
    p = {
        "id": _next_id(data, parse_date(rec.get("first_seen"))),
        "problem": text,
        "topic": rec.get("topic") or "未分类",
        "points": list(rec.get("points") or []) or ["未分类"],
        "kind": rec.get("kind") or "",
        "my_work": rec.get("my_work") or "",
        "stuck": rec.get("stuck") or "",
        "answer": rec.get("answer") or "",
        "fix": rec.get("fix") or "",
        "source": rec.get("source") or "",
        "first_seen": rec.get("first_seen") or date.today().isoformat(),
        "solved": bool(rec.get("solved")),
        "solved_at": rec.get("solved_at"),
        "starred": bool(rec.get("starred")),
    }
    if p["topic"] == "未分类" and p["points"] and p["points"][0] != "未分类":
        pass
    data["problems"].append(p)
    return p, True


def _merge(dst: dict, rec: dict) -> list[str]:
    changed: list[str] = []
    for k in ("topic", "kind", "my_work", "stuck", "answer", "fix"):
        if not dst.get(k) and rec.get(k):
            dst[k] = rec[k]
            changed.append(k)
    pts = dst.setdefault("points", [])
    for pt in rec.get("points") or []:
        if pt not in pts:
            pts.append(pt)
            changed.append("points")
    if "未分类" in pts and len(pts) > 1:
        pts.remove("未分类")
    src = (rec.get("source") or "").strip()
    if src and src not in (dst.get("source") or ""):
        old = (dst.get("source") or "").strip()
        dst["source"] = f"{old}；{src}" if old else src
        changed.append("source")
    return changed


# ---------------------------------------------------------------- 统计

def point_stats(data: dict) -> dict:
    """知识点 → 未搞懂数 / 总数 / 最近一次 / 题号。"""
    st: dict[str, dict] = {}
    for p in data["problems"]:
        for pt in (p.get("points") or ["未分类"]):
            d = st.setdefault(pt, {"total": 0, "open": 0, "last": "", "ids": []})
            d["total"] += 1
            if not p.get("solved"):
                d["open"] += 1
            d["last"] = max(d["last"], p.get("first_seen") or "")
            d["ids"].append(p.get("id") or "")
    return st


def weak_points(data: dict) -> list[tuple[str, dict]]:
    """SPEC §8：未搞懂多的在前，其次总数多的，再次最近错的在前。"""
    items = list(point_stats(data).items())
    items.sort(key=lambda kv: (
        -kv[1]["open"], -kv[1]["total"], _neg_date(kv[1]["last"]), kv[0],
    ))
    return [kv for kv in items if kv[1]["open"] > 0]


def _neg_date(s: str) -> str:
    """让 ISO 日期在升序里表现为倒序。"""
    return "".join(chr(0x10FFFF - ord(c)) for c in (s or ""))


def topic_rank(topic: str) -> tuple[int, str]:
    if topic in TOPIC_ORDER:
        return (TOPIC_ORDER.index(topic), "")
    return (len(TOPIC_ORDER) + (1 if topic == "未分类" else 0), topic)


def sort_study(items: list[dict], weak: dict) -> list[dict]:
    """反复错的知识点先出现：先按知识点未搞懂数降序，再按日期降序（稳定排序两趟）。

    weak: 知识点 → 未搞懂题数。
    """
    def w(p: dict) -> int:
        return max([weak.get(pt, 0) for pt in (p.get("points") or [])] or [0])

    out = sorted(items, key=lambda p: (p.get("first_seen") or "", p.get("id") or ""), reverse=True)
    out.sort(key=lambda p: -w(p))
    return out


# ---------------------------------------------------------------- 命令：add

LATEX_HINT = re.compile(r"\\[a-zA-Z]{2,}|\$")


def _latex_warn(text: str) -> bool:
    return bool(LATEX_HINT.search(text or ""))


def _rec_from_args(args) -> dict:
    return {
        "problem": args.problem or "",
        "topic": args.topic or "",
        "points": list(args.point or []),
        "kind": args.kind or "",
        "my_work": args.my_work or "",
        "stuck": args.stuck or "",
        "answer": args.answer or "",
        "fix": args.fix or "",
        "source": args.source or "",
        "first_seen": args.date or date.today().isoformat(),
        "starred": bool(getattr(args, "star", False)),
    }


def cmd_add(args) -> int:
    data = load()
    recs: list[dict] = []
    if args.file:
        raw = read_json(Path(args.file))
        if isinstance(raw, dict):
            recs = list(raw.get("problems") or [])
        elif isinstance(raw, list):
            recs = raw
        else:
            raise SystemExit("批量文件格式：{\"problems\": [...]} 或一个数组")
    if args.problem:
        recs.append(_rec_from_args(args))
    if not recs:
        raise SystemExit("没有要记的题：给一个题干，或用 --file 导入")

    created = merged = 0
    latex: list[str] = []
    nopoint: list[str] = []
    for rec in recs:
        if not one_line(rec.get("problem")):
            print(f"✗ 跳过一条：题干为空", file=sys.stderr)
            continue
        rec.setdefault("first_seen", args.date or date.today().isoformat())
        if not rec.get("points"):
            nopoint.append(one_line(rec["problem"])[:24])
            rec["points"] = ["未分类"]
        p, is_new = upsert(data, rec)
        created += is_new
        merged += 0 if is_new else 1
        if _latex_warn(p.get("problem")) or _latex_warn(p.get("stuck")) or _latex_warn(p.get("fix")):
            latex.append(p["id"])
        mark = "+" if is_new else "="
        pts = " / ".join(p.get("points") or [])
        print(f"{mark} {p['id']}  {p.get('topic')} · {pts}  {cut(one_line(p['problem']), 42)}")

    save(data)
    log(f"add: 新增 {created}，合并 {merged}")
    print(f"\n记下 {created} 道新题" + (f"，合并 {merged} 道已有的（只补了空缺）" if merged else "") + "。")
    if nopoint:
        print(f"⚠ {len(nopoint)} 道题没给知识点，暂时算「未分类」——不补的话周报统计不到它：")
        for t in nopoint[:4]:
            print(f"   {t}")
    if latex:
        print(f"⚠ {len(latex)} 道题的题干/卡点里有 LaTeX 痕迹（反斜杠、$、^{{}}）；"
              f"报告不联网、不渲染 LaTeX，建议改成 Unicode：见 SPEC.md §11")
    return 0


# ---------------------------------------------------------------- 命令：solve / unsolve / star / unstar

def _mark(args, field: str, value: bool) -> int:
    data = load()
    keys = list(args.keys or [])
    if not keys:
        raise SystemExit("要给至少一个 id 或题干片段")
    done, failed = [], []
    for key in keys:
        p, err = resolve(data, key)
        if err:
            failed.append(err)
            print(f"✗ {err}", file=sys.stderr)
            continue
        if field == "solved":
            new = not p.get("solved") if args.toggle else value
            p["solved"] = new
            p["solved_at"] = date.today().isoformat() if new else None
            label = "搞懂了" if new else "放回还没搞懂"
        else:
            new = not p.get("starred") if args.toggle else value
            p["starred"] = new
            label = "收藏" if new else "取消收藏"
        done.append(p)
        print(f"✓ {p['id']}  {label}  {cut(one_line(p['problem']), 40)}")
    if done:
        save(data)
        log(f"{field}={value}: {len(done)} 条")
    if failed and not done:
        return 1
    if field == "solved" and value:
        left = sum(1 for p in data["problems"] if not p.get("solved"))
        print(f"\n还剩 {left} 道没搞懂。")
    return 0


def cmd_solve(args) -> int:
    return _mark(args, "solved", True)


def cmd_unsolve(args) -> int:
    return _mark(args, "solved", False)


def cmd_star(args) -> int:
    return _mark(args, "starred", True)


def cmd_unstar(args) -> int:
    return _mark(args, "starred", False)


# ---------------------------------------------------------------- 命令：list / points / stats / weeks

def _status_marks(p: dict) -> str:
    return ("✓" if p.get("solved") else "·") + ("★" if p.get("starred") else " ")


def cmd_list(args) -> int:
    data = load()
    items = list(data["problems"])
    if args.solved:
        items = [p for p in items if p.get("solved")]
    elif args.starred:
        items = [p for p in items if p.get("starred")]
    elif not args.all:
        items = [p for p in items if not p.get("solved")]
    if args.point:
        items = [p for p in items if args.point.casefold() in [x.casefold() for x in (p.get("points") or [])]]
    if args.topic:
        items = [p for p in items if p.get("topic") == args.topic]
    if args.kind:
        items = [p for p in items if p.get("kind") == args.kind]
    if args.week:
        monday = parse_week(args.week)
        items = [p for p in items if in_range(p.get("first_seen"), monday, monday + timedelta(days=6))]
    items.sort(key=lambda p: (p.get("first_seen") or "", p.get("id") or ""), reverse=True)

    if not items:
        print("（没有符合条件的题）")
        return 0
    widths = (15, 3, 10, 22, 6, 11)
    head = "".join(pad(t, w) for t, w in zip(("id", " ", "模块", "知识点", "错因", "记于"), widths))
    print(head + "题干")
    print("─" * 100)
    for p in items:
        pts = cut("、".join(p.get("points") or []), widths[3])
        row = "".join(pad(t, w) for t, w in zip(
            (p.get("id") or "", _status_marks(p), cut(p.get("topic") or "", widths[2]),
             pts, cut(p.get("kind") or "—", widths[4]), p.get("first_seen") or ""), widths))
        print(row + cut(one_line(p.get("problem")), 46))
    opened = sum(1 for p in items if not p.get("solved"))
    print(f"\n共 {len(items)} 道（还没搞懂 {opened} / 搞懂了 {len(items) - opened}）")
    return 0


def cmd_points(args) -> int:
    data = load()
    st = point_stats(data)
    if not st:
        print("（账本还是空的）")
        return 0
    items = sorted(st.items(), key=lambda kv: (-kv[1]["open"], -kv[1]["total"],
                                               _neg_date(kv[1]["last"]), kv[0]))
    if not args.all:
        items = [kv for kv in items if kv[1]["open"] > 0]
    if not items:
        print("（所有知识点都搞懂了）")
        return 0
    print(f"{pad('知识点', 24)}{pad('没搞懂', 9)}{pad('总数', 6)}{pad('最近', 12)}题号")
    print("─" * 92)
    for pt, d in items:
        flag = " ← 反复错" if d["open"] >= 2 else ""
        print(f"{pad(cut(pt, 24), 24)}{pad(str(d['open']), 9)}{pad(str(d['total']), 6)}"
              f"{pad(d['last'] or '—', 12)}{cut('、'.join(d['ids']), 30)}{flag}")
    total_open = sum(1 for p in data["problems"] if not p.get("solved"))
    print(f"\n{len(items)} 个知识点还没搞懂（共 {total_open} 道题）。"
          f"「反复错」= 同一知识点下有 2 道以上还没搞懂。")
    return 0


def cmd_stats(args) -> int:
    data = load()
    ps = data["problems"]
    if not ps:
        print("账本还是空的。用 add 记第一道题。")
        return 0
    opened = [p for p in ps if not p.get("solved")]
    monday = week_monday(date.today())
    sunday = monday + timedelta(days=6)
    this_week = [p for p in ps if in_range(p.get("first_seen"), monday, sunday)]
    solved_week = [p for p in ps if in_range(p.get("solved_at"), monday, sunday)]
    print(f"错题账本 · {DATA_DIR}")
    print("─" * 56)
    print(f"共 {len(ps)} 道  还没搞懂 {len(opened)}  搞懂了 {len(ps) - len(opened)}")
    print(f"本周（{week_key(monday)}）新增 {len(this_week)}，搞懂 {len(solved_week)}")
    kinds = {}
    for p in ps:
        k = p.get("kind") or "未标"
        kinds[k] = kinds.get(k, 0) + 1
    if kinds:
        print("\n错因分布：")
        for k, n in sorted(kinds.items(), key=lambda kv: -kv[1]):
            print(f"  {pad(k, 6)}{n:>3} 道")
    weak = weak_points(data)[:6]
    if weak:
        print("\n最该补的知识点：")
        for pt, d in weak:
            print(f"  {pad(cut(pt, 22), 22)}没搞懂 {d['open']} / 共 {d['total']}   最近 {d['last']}")
    topics = {}
    for p in ps:
        t = p.get("topic") or "未分类"
        topics[t] = topics.get(t, 0) + 1
    print("\n模块分布：" + "  ".join(f"{t} {n}" for t, n in sorted(topics.items(), key=lambda kv: topic_rank(kv[0]))))
    return 0


def cmd_weeks(args) -> int:
    data = load()
    ps = data["problems"]
    if not ps:
        print("（账本还是空的）")
        return 0
    added: dict[str, int] = {}
    solved: dict[str, int] = {}
    for p in ps:
        if p.get("first_seen"):
            k = week_key(parse_date(p["first_seen"]))
            added[k] = added.get(k, 0) + 1
        if p.get("solved_at"):
            k = week_key(parse_date(p["solved_at"]))
            solved[k] = solved.get(k, 0) + 1
    keys = sorted(set(added) | set(solved))
    print(f"{pad('周', 12)}{pad('新增', 6)}搞懂")
    print("─" * 28)
    for k in keys:
        print(f"{pad(k, 12)}{pad(str(added.get(k, 0)), 6)}{solved.get(k, 0)}")
    return 0


# ---------------------------------------------------------------- HTML 周报

CSS = """
:root{
  --ink:#1c1b19; --muted:#736d64; --line:#e4dfd4; --paper:#fffdf8;
  --accent:#14604f; --accent-soft:#e9f2ed;
  --warn:#a8401d; --warn-soft:#fbeee7;
  --amber:#8a5a00; --amber-soft:#fdf4de;
  --blue:#2a4d69; --blue-soft:#eaf0f6;
}
*{box-sizing:border-box}
html{-webkit-print-color-adjust:exact; print-color-adjust:exact}
body{
  margin:0; padding:28px 18px 60px; background:#f2efe7; color:var(--ink);
  font-family:"Segoe UI","Microsoft YaHei","PingFang SC",system-ui,sans-serif;
  line-height:1.65; font-size:15px;
}
.page{
  max-width:880px; margin:0 auto; background:var(--paper);
  border:1px solid var(--line); border-radius:10px;
  padding:40px 44px 48px; box-shadow:0 2px 10px rgba(60,50,30,.06);
}
header{border-bottom:3px double var(--line); padding-bottom:18px; margin-bottom:24px}
.kicker{font-size:12px; letter-spacing:.18em; color:var(--accent); font-weight:700; text-transform:uppercase}
h1{font-size:29px; margin:6px 0 4px; letter-spacing:.01em; font-weight:700}
.meta{font-size:12.5px; color:var(--muted)}

.stats{display:grid; grid-template-columns:repeat(4,1fr); gap:10px; margin:0 0 28px}
.stat{background:var(--accent-soft); border:1px solid #d8e6de; border-radius:8px; padding:11px 8px; text-align:center}
.stat.amber{background:var(--amber-soft); border-color:#eddfbd}
.stat .n{font-size:23px; font-weight:700; color:var(--accent); line-height:1.2}
.stat.amber .n{color:var(--amber)}
.stat .l{font-size:11.5px; color:var(--muted); letter-spacing:.04em}

.topbar{display:flex; flex-direction:column; gap:10px; align-items:stretch;
  margin:0 0 18px; padding-bottom:12px; border-bottom:1px solid var(--line)}
.tabbar{display:flex; gap:7px; flex-wrap:wrap}
.tab{font-size:12.5px; text-decoration:none; color:#3f3a33; border:1px solid var(--line);
  border-radius:99px; padding:6px 12px; background:#fffefa; cursor:pointer; user-select:none}
.tab b{color:var(--accent); margin-left:5px; font-weight:600}
.tab:hover{border-color:var(--accent); color:var(--accent)}
.tab.on{background:var(--accent); border-color:var(--accent); color:#fff}
.tab.on b{color:#fff}
.topbar .search{width:100%}
input.search{
  width:100%; padding:9px 12px; font:inherit; font-size:13px; color:var(--ink);
  border:1px solid var(--line); border-radius:8px; background:#fffefa; outline:none;
}
input.search:focus{border-color:var(--accent)}

.panel{display:none}
.panel.on{display:block}
h2{font-size:17px; margin:26px 0 10px; padding-left:10px; border-left:4px solid var(--accent); line-height:1.35}
h2 .count{font-size:12px; color:var(--muted); font-weight:400; margin-left:6px}
.hint{font-size:12.5px; color:var(--muted); margin:-2px 0 14px}

.cards{display:grid; grid-template-columns:1fr; gap:14px}
.card{border:1px solid var(--line); border-radius:9px; padding:14px 16px 12px; background:#fffefa;
  break-inside:avoid; transition:opacity .18s, transform .18s; position:relative}
.card.masked{cursor:pointer}
.card.masked:hover{border-color:#cdc5b5}
.revmeta{display:flex; align-items:center; flex-wrap:wrap; gap:8px; font-size:11.5px; color:var(--muted); margin-bottom:6px}
.revmeta .topic{background:var(--blue-soft); color:var(--blue); border-radius:4px; padding:1px 7px; font-size:10.5px}
.revmeta .pt{color:var(--accent); font-weight:600; font-size:12px}
.revmeta .kind{background:var(--warn-soft); color:var(--warn); border-radius:4px; padding:1px 7px; font-size:10.5px}
.revmeta .dt{font-variant-numeric:tabular-nums}
.stem{
  font-family:"Cambria","Latin Modern Math","Songti SC","SimSun",serif;
  font-size:15.5px; line-height:1.85; margin:2px 0 8px; white-space:pre-wrap; word-break:break-word;
}
.stem .idx{font-family:"Segoe UI",sans-serif; font-size:11.5px; color:var(--muted); margin-right:8px}

.answer{margin-top:8px; border-top:1px dashed var(--line); padding-top:9px; display:block}
.card.masked .answer{display:none}
.card.masked.reveal .answer{display:block}
.card.masked .tip{display:block}
.card.masked.reveal .tip{display:none}
.tip{font-size:11.5px; color:var(--muted); opacity:.6; margin-top:2px}
.ansline{display:none}
.ansline span{font-size:11.5px; color:var(--muted)}
.row{display:flex; gap:9px; font-size:13px; margin:5px 0; align-items:baseline}
.row .lb{flex:0 0 62px; font-size:11.5px; color:var(--muted); letter-spacing:.04em; text-align:right}
.row .v{flex:1; min-width:0; white-space:pre-wrap; word-break:break-word}
.row.warn .v{color:var(--warn); background:var(--warn-soft); border-radius:5px; padding:2px 8px}
.row.fix .v{color:#14503f; background:var(--accent-soft); border-radius:5px; padding:2px 8px}
.row.ans .v{font-weight:700; font-family:"Cambria","Songti SC",serif; font-size:15px}
.row.src .v{color:var(--muted); font-size:11.5px}

.group{margin-bottom:24px}
.group-head{display:flex; align-items:baseline; gap:10px; margin:20px 0 10px; padding-bottom:6px;
  border-bottom:1px dashed var(--line); flex-wrap:wrap}
.group-head .t{font-size:15px; font-weight:700; color:var(--accent)}
.group-head .c{font-size:11.5px; color:var(--muted)}
.group-head .hot{font-size:10.5px; background:var(--warn-soft); color:var(--warn); border-radius:99px; padding:1px 9px}

.acts{display:flex; gap:6px; margin-left:auto; flex:0 0 auto}
.act{font:inherit; font-size:12px; line-height:1; padding:7px 11px; border-radius:8px; cursor:pointer;
  border:1px solid var(--line); background:#fff; color:#6f6a63; min-width:64px; white-space:nowrap}
.act:hover{border-color:#c9c2b4}
.act.star.on{background:var(--amber-soft); border-color:#e3c27a; color:var(--amber)}
.act.done.on{background:var(--accent-soft); border-color:#cfe0d6; color:var(--accent)}
.badge{font-size:10.5px; padding:2px 8px; border-radius:99px; border:1px solid transparent}
.badge.star{background:var(--amber-soft); color:var(--amber); border-color:#e3c27a}
.badge.done{background:var(--accent-soft); color:var(--accent); border-color:#cfe0d6}

.chips{display:flex; flex-wrap:wrap; gap:8px; margin:0 0 14px}
.chip{border:1px solid #cfe0d6; background:var(--accent-soft); color:#14503f; border-radius:99px; padding:3px 12px; font-size:12.5px}
.chip .s{font-weight:600; margin-left:4px}
.chip[data-q]{cursor:pointer}
.chip[data-q]:hover{border-color:var(--accent); background:#fff}
.chip.amber{border-color:#eddfbd; background:var(--amber-soft); color:var(--amber)}

.answer-key{display:none}
.answer-key h2{margin-top:0}
.keyitem{border-bottom:1px solid var(--line); padding:7px 0; font-size:12.5px; break-inside:avoid}
.keyitem .kid{font-family:Consolas,monospace; font-size:11.5px; color:var(--muted); margin-right:8px}
.keyitem .kv{display:block; margin-top:2px}
.keyitem .kv b{color:var(--muted); font-weight:600; margin-right:6px; font-size:11.5px}

.empty{color:var(--muted); font-size:13px; padding:10px 0}
footer{margin-top:36px; padding-top:14px; border-top:1px solid var(--line); font-size:11.5px; color:var(--muted)}
.toolbar{position:fixed; right:22px; bottom:22px}
.toolbar button{
  font:inherit; font-size:13px; padding:10px 18px; border-radius:99px; cursor:pointer;
  border:1px solid var(--accent); background:var(--accent); color:#fff; box-shadow:0 3px 10px rgba(0,0,0,.15)
}

@media print{
  body{background:#fff; padding:0; font-size:10.5pt}
  .page{max-width:none; border:0; box-shadow:none; padding:0; border-radius:0}
  .no-print{display:none !important}
  header{margin-bottom:14px}
  .topbar{display:none}
  .stats{grid-template-columns:repeat(4,1fr); margin-bottom:12px}
  .panel{display:none}
  .panel.on{display:block}
  .card{break-inside:avoid; padding:10px 0; border:0; border-bottom:1px solid var(--line); border-radius:0}
  .answer{display:block}
  .panel[data-blank="1"] .answer{display:none}
  .panel[data-blank="1"] .tip{display:none}
  .panel[data-blank="1"] .ansline{display:flex; align-items:flex-end; gap:5px; margin:8px 0 2px}
  .panel[data-blank="1"] .ansline .line{flex:1; height:19px; border-bottom:1px dotted #cfc7b7}
  .panel[data-blank="1"] .answer-key{display:block; break-before:page}
  .acts,.badge{display:none}
  h2{break-after:avoid; margin-top:18px}
  .group-head{break-after:avoid}
  .group{break-inside:auto}
  .cards{display:block}
  @page{size:A4; margin:14mm}
}
@media (max-width:640px){
  .stats{grid-template-columns:repeat(2,1fr)}
  .page{padding:24px 18px}
}
"""

PANELS = (
    ("study", "过一遍"),
    ("weak", "薄弱知识点"),
    ("t1", "表一 · 本周新增"),
    ("t2", "还没搞懂"),
    ("fav", "收藏夹"),
    ("solved", "搞懂了"),
)

APP_JS = r"""
let tab = 'study';

function panelOf(name){ return document.querySelector('.panel[data-panel="' + name + '"]'); }
function bodyOf(p){ return p.querySelector('.body'); }

function filter(){
  const q = (document.getElementById('q').value || '').trim().toLowerCase();
  document.querySelectorAll('.panel').forEach(p => {
    const name = p.dataset.panel;
    p.querySelectorAll('.card').forEach(c => {
      let ok = !q || c.textContent.toLowerCase().indexOf(q) > -1;
      if (ok) {
        const solved = c.dataset.solved === '1';
        const star = c.dataset.star === '1';
        if (name === 'study' || name === 't2' || name === 'weak') ok = !solved;
        else if (name === 'fav') ok = star;
        else if (name === 'solved') ok = solved;
      }
      c.style.display = ok ? '' : 'none';
    });
    // 组内都没了就整组收起来，别留一个空标题
    p.querySelectorAll('.group').forEach(g => {
      const any = [...g.querySelectorAll('.card')].some(c => c.style.display !== 'none');
      g.style.display = any ? '' : 'none';
    });
  });
}

function show(name){
  tab = name;
  document.querySelectorAll('.panel').forEach(p => p.classList.toggle('on', p.dataset.panel === name));
  document.querySelectorAll('.tabbar .tab').forEach(a => a.classList.toggle('on', a.dataset.panel === name));
  filter();
}

async function refreshPanels(){
  try {
    const r = await fetch('/', { cache: 'no-store' });
    if (!r.ok) return;
    const doc = new DOMParser().parseFromString(await r.text(), 'text/html');
    document.querySelectorAll('.panel').forEach(p => {
      const src = doc.querySelector('.panel[data-panel="' + p.dataset.panel + '"]');
      if (src) bodyOf(p).innerHTML = bodyOf(src).innerHTML;
    });
    // 顶部计数和统计块也在服务端算，一并换掉，别让「页面」和「文件」对不上
    ['stats', 'tabbar'].forEach(cls => {
      const cur = doc.querySelector('.' + cls);
      const old = document.querySelector('.' + cls);
      if (cur && old) old.innerHTML = cur.innerHTML;
    });
    filter();
  } catch (e) { /* 刷新失败就保持现状 */ }
}

async function act(card, field){
  const isStar = field === 'star';
  const current = isStar ? card.dataset.star : card.dataset.solved;
  const body = { id: card.dataset.id, field: isStar ? 'star' : 'solved', value: current !== '1' };
  try {
    const r = await fetch('/api/mark', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error('HTTP ' + r.status);
  } catch (e) { alert('写入失败：' + e.message); return; }
  await refreshPanels();   // 以服务端为准重绘，计数和列表一起更新
}

document.addEventListener('click', e => {
  const btn = e.target.closest('.act');
  if (btn) { e.stopPropagation(); act(btn.closest('.card'), btn.dataset.act); return; }
  const a = e.target.closest('.tabbar .tab');
  if (a) { show(a.dataset.panel); return; }
  const chip = e.target.closest('.chip[data-q]');
  if (chip) {   // 点知识点标签：跳到「还没搞懂」并按它过滤
    document.getElementById('q').value = chip.dataset.q;
    show('t2');
    return;
  }
  const card = e.target.closest('.card.masked');
  if (card) card.classList.toggle('reveal');   // 点卡片揭晓
});

document.addEventListener('keydown', e => {
  if (e.target.tagName === 'INPUT') return;
  if (e.key === '1' || e.key === '2') {
    const card = document.querySelector('.panel.on .card.masked') || document.querySelector('.panel.on .card');
    if (card) { e.preventDefault(); act(card, e.key === '1' ? 'star' : 'solved'); }
  }
});

setInterval(() => { fetch('/ping').catch(() => {}); }, 60000);
const initial = new URLSearchParams(location.search).get('tab');
if (initial && document.querySelector('.panel[data-panel="' + initial + '"]')) show(initial);
else filter();
"""


BLANK_ATTR = ' data-blank="1"'


def esc(s) -> str:
    return html.escape(str(s or ""), quote=True)


def week_ordinal(data: dict, monday: date) -> int:
    """从账本里最早那道题所在的周算第 1 周。"""
    days = []
    for p in data["problems"]:
        try:
            if p.get("first_seen"):
                days.append(date.fromisoformat(p["first_seen"]))
        except (ValueError, TypeError):
            pass
    if not days:
        return 1
    first = week_monday(min(days))
    return max(1, (monday - first).days // 7 + 1)


def _week_stats(data: dict, monday: date) -> dict:
    sunday = monday + timedelta(days=6)
    ps = data["problems"]
    weak = {k: v["open"] for k, v in point_stats(data).items()}

    def desc(items):
        return sorted(items, key=lambda p: (p.get("first_seen") or "", p.get("id") or ""), reverse=True)

    week_problems = desc([p for p in ps if in_range(p.get("first_seen"), monday, sunday)])
    open_problems = sort_study([p for p in ps if not p.get("solved")], weak)
    solved_week = desc([p for p in ps if in_range(p.get("solved_at"), monday, sunday)])
    starred = desc([p for p in ps if p.get("starred")])
    solved_all = sorted([p for p in ps if p.get("solved")],
                        key=lambda p: (p.get("solved_at") or "", p.get("id") or ""), reverse=True)
    weak_list = weak_points(data)
    return {
        "week_problems": week_problems,
        "open_problems": open_problems,
        "solved_week": solved_week,
        "starred": starred,
        "solved_all": solved_all,
        "weak_list": weak_list,
        "repeat_points": sum(1 for _, d in weak_list if d["open"] >= 2),
        "total": len(ps),
        "open_total": len([p for p in ps if not p.get("solved")]),
    }


def _row(label: str, value: str, cls: str = "") -> str:
    if not value:
        return ""
    c = f"row {cls}".strip()
    return f'<div class="{c}"><span class="lb">{esc(label)}</span><span class="v">{esc(value)}</span></div>'


def _problem_card(p: dict, interactive: bool, meta: str = "", masked: bool = True) -> str:
    pts = " · ".join(p.get("points") or [])
    bits = []
    if p.get("topic") and p.get("topic") != "未分类":
        bits.append(f'<span class="topic">{esc(p["topic"])}</span>')
    if pts:
        bits.append(f'<span class="pt">{esc(pts)}</span>')
    if p.get("kind"):
        bits.append(f'<span class="kind">{esc(p["kind"])}</span>')
    if meta:
        bits.append(f'<span class="dt">{esc(meta)}</span>')

    if interactive:
        star = ' class="act star on"' if p.get("starred") else ' class="act star"'
        done = ' class="act done on"' if p.get("solved") else ' class="act done"'
        acts = (f'<div class="acts">'
                f'<button{star} data-act="star">{"★ 已收藏" if p.get("starred") else "☆ 收藏"}</button>'
                f'<button{done} data-act="solved">{"✓ 搞懂了" if p.get("solved") else "搞懂了"}</button>'
                f'</div>')
    else:
        badge = []
        if p.get("starred"):
            badge.append('<span class="badge star">★</span>')
        if p.get("solved"):
            badge.append(f'<span class="badge done">搞懂了 {esc(p.get("solved_at") or "")}</span>')
        acts = f'<div class="acts">{"".join(badge)}</div>' if badge else ""

    dataset = (f' data-id="{esc(p.get("id"))}" data-solved="{1 if p.get("solved") else 0}" '
               f'data-star="{1 if p.get("starred") else 0}"')
    cls = "card masked" if masked else "card"

    body = (
        _row("我的错解", p.get("my_work") or "")
        + _row("断在哪", p.get("stuck") or "", "warn")
        + f'<div class="row ans"><span class="lb">答案</span><span class="v">{esc(p.get("answer") or "—")}</span></div>'
        + _row("该怎么想", p.get("fix") or "", "fix")
        + _row("出处", p.get("source") or "", "src")
    )
    return (
        f'<article class="{cls}"{dataset}>'
        f'<div class="revmeta">{"".join(bits)}{acts}</div>'
        f'<div class="stem"><span class="idx">#{esc(p.get("id"))}</span>{esc(p.get("problem"))}</div>'
        + ('<div class="ansline"><span>答：</span><span class="line"></span></div>' if masked else "")
        + ('<div class="tip">先自己重做一遍，再点开对答案</div>' if masked else "")
        + f'<div class="answer">{body}</div>'
        + "</article>"
    )


def _answer_key(items: list[dict], wnum: int) -> str:
    if not items:
        return ""
    out = ['<div class="answer-key no-print-hidden">',
           f'<h2>答案 · 第 {wnum} 周<span class="count">{len(items)} 道</span></h2>']
    for p in items:
        parts = []
        parts.append(f'<span class="kv"><b>答案</b>{esc(p.get("answer") or "—")}</span>')
        if p.get("stuck"):
            parts.append(f'<span class="kv"><b>断点</b>{esc(p["stuck"])}</span>')
        if p.get("fix"):
            parts.append(f'<span class="kv"><b>该怎么想</b>{esc(p["fix"])}</span>')
        out.append(f'<div class="keyitem"><span class="kid">#{esc(p.get("id"))}</span>'
                   f'{esc(cut(one_line(p.get("problem")), 60))}'
                   + "".join(parts) + "</div>")
    out.append("</div>")
    return "".join(out)


def _cards(items: list[dict], interactive: bool, masked: bool, meta_fn=None) -> str:
    meta_fn = meta_fn or (lambda p: p.get("first_seen") or "")
    return "".join(_problem_card(p, interactive, meta_fn(p), masked) for p in items)


def _grouped(items: list[dict], interactive: bool, key_fn, order_fn, head_fn) -> str:
    groups: dict[str, list[dict]] = {}
    for p in items:
        groups.setdefault(key_fn(p), []).append(p)
    out = []
    for name in sorted(groups, key=order_fn):
        out.append(f'<div class="group"><div class="group-head">{head_fn(name, groups[name])}</div>'
                   f'<div class="cards">'
                   + _cards(groups[name], interactive, masked=True)
                   + "</div></div>")
    return "".join(out)


def render_app(data: dict, monday: date, max_review: int = 80,
               title: str | None = None, interactive: bool = False) -> str:
    """一页 HTML，六个可切换的视图，全部用卡片。"""
    sunday = monday + timedelta(days=6)
    wk = week_key(monday)
    wnum = week_ordinal(data, monday)
    st = _week_stats(data, monday)
    shown = st["open_problems"][:max_review]

    kind_dist: dict[str, int] = {}
    for p in st["week_problems"]:
        k = p.get("kind") or "未标"
        kind_dist[k] = kind_dist.get(k, 0) + 1
    kind_chips = "".join(
        f'<span class="chip{" amber" if k == "未标" else ""}">{esc(k)} <span class="s">{n}</span></span>'
        for k, n in sorted(kind_dist.items(), key=lambda kv: (-kv[1], kv[0]))
    )

    # ---- study：先自己重做，点开对答案
    study_body = (_cards(shown, interactive, masked=True) or
                  '<div class="empty">没有还没搞懂的题。去「搞懂了」看看已经拿下什么。</div>')
    study = (f'<div class="body">{study_body}</div>'
             + _answer_key(shown, wnum))

    # ---- weak：数学版核心视图。反复错的摊开，只错一次的排成标签
    by_id = {p.get("id"): p for p in data["problems"]}
    repeated = [(pt, d) for pt, d in st["weak_list"] if d["open"] >= 2]
    singles = [(pt, d) for pt, d in st["weak_list"] if d["open"] < 2]
    weak_parts = []
    if repeated:
        weak_parts.append('<h2>反复错的<span class="count">同一个断点栽了不止一次</span></h2>')
        for pt, d in repeated:
            items = [by_id[i] for i in d["ids"] if i in by_id and not by_id[i].get("solved")]
            items.sort(key=lambda p: (p.get("first_seen") or "", p.get("id") or ""), reverse=True)
            head = (f'<span class="t">{esc(pt)}</span><span class="hot">反复错</span>'
                    f'<span class="c">没搞懂 {d["open"]} / 共 {d["total"]} · 最近 {esc(d["last"] or "—")}</span>')
            weak_parts.append(f'<div class="group"><div class="group-head">{head}</div>'
                              f'<div class="cards">{_cards(items, interactive, masked=True)}</div></div>')
    if singles:
        weak_parts.append('<h2>只错过一次的<span class="count">大概率是偶发，先把上面的补掉</span></h2>')
        chips = "".join(
            f'<span class="chip" data-q="{esc(pt)}" title="在「还没搞懂」里筛这个知识点">'
            f'{esc(pt)}<span class="s">{d["open"]}</span></span>'
            for pt, d in singles)
        weak_parts.append(f'<div class="chips">{chips}</div>')
    weak_body = "".join(weak_parts) or '<div class="empty">还没有没搞懂的知识点。</div>'
    weak = f'<div class="body">{weak_body}</div>'

    # ---- t1：本周新增
    t1_body = (_cards(st["week_problems"], interactive, masked=False,
                      meta_fn=lambda p: f'{p.get("first_seen") or ""}')
               or '<div class="empty">这周没有新记的题。</div>')
    t1 = (f'<div class="body"><div class="chips dist">{kind_chips}</div>{t1_body}</div>')

    # ---- t2：全部还没搞懂，按模块分组
    t2_body = _grouped(
        st["open_problems"], interactive,
        key_fn=lambda p: p.get("topic") or "未分类",
        order_fn=lambda t: topic_rank(t),
        head_fn=lambda t, items: (f'<span class="t">{esc(t)}</span>'
                                  f'<span class="c">{len(items)} 道还没搞懂</span>'),
    ) or '<div class="empty">全部搞懂了。</div>'
    t2 = (f'<div class="body">{t2_body}</div>'
          + _answer_key(st["open_problems"][:max_review], wnum))

    # ---- fav：收藏夹
    fav_body = _cards(st["starred"], interactive, masked=False) or '<div class="empty">还没有收藏的题。</div>'
    fav = f'<div class="body">{fav_body}</div>'

    # ---- solved：搞懂了
    solved_body = (_cards(st["solved_all"], interactive, masked=False,
                          meta_fn=lambda p: f'搞懂于 {p.get("solved_at") or "—"}')
                   or '<div class="empty">还没有搞懂的题。</div>')
    solved = f'<div class="body">{solved_body}</div>'

    bodies = {"study": study, "weak": weak, "t1": t1, "t2": t2, "fav": fav, "solved": solved}
    counts = {
        "study": len(st["open_problems"]),
        "weak": len(st["weak_list"]),
        "t1": len(st["week_problems"]),
        "t2": len(st["open_problems"]),
        "fav": len(st["starred"]),
        "solved": len(st["solved_all"]),
    }

    tabs = "".join(
        f'<a class="tab{" on" if key == "study" else ""}" data-panel="{key}" id="tab-{key}">'
        f'{esc(label)}<b id="n-{key}">{counts[key]}</b></a>'
        for key, label in PANELS
    )
    panels = "".join(
        f'<section class="panel{" on" if key == "study" else ""}" data-panel="{key}" id="p-{key}"'
        f'{BLANK_ATTR if key in ("study", "t2") else ""}>{bodies[key]}</section>'
        for key, _ in PANELS
    )

    page_title = title or (f"第 {wnum} 周 · 错题报告")
    stats = (
        f'<div class="stat"><div class="n">{counts["t1"]}</div><div class="l">本周新增</div></div>'
        f'<div class="stat"><div class="n">{counts["t2"]}</div><div class="l">还没搞懂</div></div>'
        f'<div class="stat"><div class="n">{len(st["solved_week"])}</div><div class="l">本周搞懂</div></div>'
        f'<div class="stat amber"><div class="n">{st["repeat_points"]}</div><div class="l">反复错的知识点</div></div>'
    )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(page_title)}</title>
<style>{CSS}</style>
</head>
<body>
<div class="page">
<header>
  <div class="kicker">MATH MISTAKES · WEEKLY</div>
  <h1>{esc(page_title)}</h1>
  <div class="meta">{monday.isoformat()} — {sunday.isoformat()} · 生成于 {now_iso()} · 共 {st["total"]} 道，还没搞懂 {st["open_total"]}</div>
</header>
<div class="stats">{stats}</div>
<div class="topbar no-print">
  <div class="tabbar">{tabs}</div>
  <div class="search"><input class="search" id="q" type="search" placeholder="搜题干、知识点、卡点、答案…"></div>
</div>
{panels}
<footer>
  本地账本 · {esc(str(DATA_FILE))} · 共 {st["total"]} 道题
  {"· 交互模式（点按钮直接写文件）" if interactive else "· 静态报告"}
  <br>打出来是一张能重做的卷子：正面题干，背面答案。
</footer>
</div>
<div class="toolbar no-print"><button onclick="window.print()">打印 / 存为 PDF</button></div>
<script>{APP_JS}</script>
</body>
</html>
"""


def render_html(data: dict, monday: date, max_review: int = 80, title: str | None = None) -> str:
    return render_app(data, monday, max_review, title, interactive=False)


def write_report(data: dict, monday: date, out: Path, max_review: int = 80,
                 title: str | None = None, touch_heartbeat: bool = True) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(data, monday, max_review, title), encoding="utf-8")
    if touch_heartbeat:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        st = _week_stats(data, monday)
        (LOG_DIR / "last-report.json").write_text(json.dumps({
            "at": now_iso(),
            "week": week_key(monday),
            "path": str(out),
            "problems": st["total"],
            "open": st["open_total"],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def week_has_data(data: dict, monday: date) -> bool:
    sunday = monday + timedelta(days=6)
    return any(in_range(p.get("first_seen"), monday, sunday) or
               in_range(p.get("solved_at"), monday, sunday) for p in data["problems"])


def latest_data_week(data: dict) -> date | None:
    days = []
    for p in data["problems"]:
        for k in ("first_seen", "solved_at"):
            if p.get(k):
                try:
                    days.append(parse_date(p[k]))
                except Exception:
                    pass
    return week_monday(max(days)) if days else None


def cmd_report(args) -> int:
    data = load()
    if args.week:
        monday = parse_week(args.week)
    else:
        monday = week_monday(date.today())
        # 本周一条都没记时，自动退回到最近有数据的一周，别让人以为坏了
        if not week_has_data(data, monday):
            latest = latest_data_week(data)
            if latest and latest != monday:
                print(f"（本周还没有数据，改用最近有数据的一周：{week_key(latest)}）")
                monday = latest
    out = Path(args.out) if args.out else REPORT_DIR / f"math-{week_key(monday)}.html"
    write_report(data, monday, out, args.max_review, args.title)
    print(f"✅ 周报已生成：{out}")
    if args.open:
        open_path(out)
    return 0


# ---------------------------------------------------------------- 调度与自检

def open_path(path: Path) -> None:
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as exc:  # pragma: no cover
        print(f"（自动打开失败：{exc}，请手动打开 {path}）", file=sys.stderr)


def last_reported_week() -> date | None:
    weeks: list[date] = []
    heartbeat = LOG_DIR / "last-report.json"
    if heartbeat.exists():
        try:
            wk = json.loads(heartbeat.read_text(encoding="utf-8")).get("week")
            if wk:
                weeks.append(parse_week(wk))
        except Exception:
            pass
    if REPORT_DIR.exists():
        for p in REPORT_DIR.glob("math-*.html"):
            m = re.match(r"math-(\d{4}-W\d{2})", p.name)
            if m:
                try:
                    weeks.append(parse_week(m.group(1)))
                except Exception:
                    pass
    return max(weeks) if weeks else None


def due_weeks(last: date | None, current: date, now: datetime, max_weeks: int = 8) -> list[date]:
    """某周的周报，在「该周周日 20:00」之后就该有了。返回所有已到期但还没出的周。"""
    start = (last + timedelta(weeks=1)) if last else (current - timedelta(weeks=max_weeks - 1))
    floor = current - timedelta(weeks=max_weeks - 1)
    if start < floor:
        start = floor
    result: list[date] = []
    monday = start
    while monday <= current:
        if now >= datetime.combine(monday + timedelta(days=6), time(20, 0)):
            result.append(monday)
        monday += timedelta(weeks=1)
    return result


def cmd_weekly(args) -> int:
    """补漏 + 出当周。幂等：没有到期的周就什么都不做。"""
    data = load()
    if not data["problems"]:
        print("账本还是空的，先记一道题再出报告。")
        return 0
    now = datetime.now()
    current = week_monday(now.date())
    pending = due_weeks(last_reported_week(), current, now, args.max_weeks)
    seen = [parse_date(p.get("first_seen")) for p in data["problems"] if p.get("first_seen")]
    if seen:
        earliest = week_monday(min(seen))
        pending = [m for m in pending if m >= earliest]
    if not pending:
        print("本周已是最新，无需生成。")
        return 0
    made: list[Path] = []
    for monday in pending:
        out = REPORT_DIR / f"math-{week_key(monday)}.html"
        write_report(data, monday, out, args.max_review)
        made.append(out)
        print(f"✅ {week_key(monday)} {out}")
    backfilled = sum(1 for m in pending if m < current)
    if backfilled and backfilled == len(made):
        print(f"（补生成了 {backfilled} 份历史周报）")
    elif backfilled:
        print(f"（含 {backfilled} 份补生成的历史周报）")
    if args.open:
        open_path(made[-1])
    return 0


def task_info(name: str) -> dict:
    if not sys.platform.startswith("win"):
        return {"error": "当前不是 Windows，跳过计划任务检查"}
    safe = "".join(ch for ch in name if ch.isalnum() or ch in "_-")
    if not safe:
        return {"error": "任务名不合法"}
    ps = (
        f"$t = Get-ScheduledTask -TaskName '{safe}' -ErrorAction Stop; "
        "$i = $t | Get-ScheduledTaskInfo; "
        "$n = ''; if ($i.NextRunTime) { $n = $i.NextRunTime.ToString('yyyy-MM-dd HH:mm') }; "
        "$l = ''; if ($i.LastRunTime -and $i.LastRunTime.Year -gt 2000) { $l = $i.LastRunTime.ToString('yyyy-MM-dd HH:mm') }; "
        "[pscustomobject]@{State=[string]$t.State; Next=$n; Last=$l; Code=[int]$i.LastTaskResult} "
        "| ConvertTo-Json -Compress"
    )
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                           capture_output=True, text=True, timeout=30)
    except Exception as exc:
        return {"error": f"查询失败：{exc}"}
    if r.returncode != 0:
        msg = (r.stderr or "").strip().splitlines()
        return {"error": msg[0][:160] if msg else "任务不存在或无权访问"}
    try:
        return json.loads(r.stdout.strip())
    except Exception:
        return {"error": "无法解析计划任务输出"}


def cmd_doctor(args) -> int:
    rows: list[tuple[str, str, str]] = []
    bad = warn = 0

    def add(level: str, label: str, value: str) -> None:
        nonlocal bad, warn
        if level == "bad":
            bad += 1
        elif level == "warn":
            warn += 1
        rows.append((level, label, value))

    add("ok" if sys.version_info >= (3, 10) else "warn", "Python",
        platform.python_version() + ("" if sys.version_info >= (3, 10) else "（建议 3.10+）"))

    if DATA_FILE.exists():
        add("ok", "账本", f"{DATA_FILE}  ({DATA_FILE.stat().st_size / 1024:.1f} KB)")
    else:
        add("warn", "账本", f"{DATA_FILE} 不存在（还没记过题）")

    try:
        data = load()
        ps = data["problems"]
        opened = sum(1 for p in ps if not p.get("solved"))
        add("ok", "题量", f"{len(ps)} 道（还没搞懂 {opened} / 搞懂了 {len(ps) - opened}）")
        no_point = [p for p in ps if not (p.get("points") or []) or p.get("points") == ["未分类"]]
        if no_point:
            add("warn", "知识点缺失", f"{len(no_point)} 道题没有知识点，统计不到它们")
        no_kind = [p for p in ps if not p.get("kind")]
        if no_kind:
            add("warn", "错因缺失", f"{len(no_kind)} 道题没标 kind，错因分布会少一块")
        no_stuck = [p for p in ps if not p.get("stuck")]
        if no_stuck:
            add("warn", "卡点缺失", f"{len(no_stuck)} 道题没写 stuck——这是最该补的字段")
        latex = [p for p in ps if _latex_warn(p.get("problem")) or _latex_warn(p.get("fix"))]
        if latex:
            add("warn", "LaTeX 痕迹", f"{len(latex)} 道题里有 LaTeX（见 SPEC §11），报告里不会渲染")
    except Exception as exc:
        add("bad", "读取账本", f"失败：{exc}")

    heartbeat = LOG_DIR / "last-report.json"
    if heartbeat.exists():
        try:
            info = json.loads(heartbeat.read_text(encoding="utf-8"))
            when = datetime.strptime(info["at"], "%Y-%m-%d %H:%M")
            age = (datetime.now() - when).days
            add("ok" if age <= 8 else "warn", "最近出报告",
                f"{info['at']}（{info.get('week')}，{info.get('problems')} 道，{age} 天前）")
        except Exception:
            add("warn", "最近出报告", "心跳文件存在但无法解析")
    else:
        add("warn", "最近出报告", "还没有生成过任何报告，跑一次 report 试试")

    if args.task:
        info = task_info(args.task)
        if "error" in info:
            add("bad", "计划任务", f"{args.task}：{info['error']}")
        else:
            code = info.get("Code", -1)
            if code == 267011:      # 0x41303：任务已注册但还没跑过
                add("ok", "计划任务",
                    f"{args.task}  {info.get('State')}  下次 {info.get('Next') or '—'}  尚未运行")
            else:
                add("ok" if code == 0 else "warn", "计划任务",
                    f"{args.task}  {info.get('State')}  下次 {info.get('Next') or '—'}  "
                    f"上次 {info.get('Last') or '从未运行'}  exit={code}")

    icon = {"ok": "✓", "warn": "⚠", "bad": "✗"}
    width = max(dw(r[1]) for r in rows) + 3
    print(f"错题账本自检 · {BASE}")
    if DATA_DIR != BASE / "data":
        print(f"（账本放在 {DATA_DIR}）")
    print("─" * 64)
    for level, label, value in rows:
        for i, line in enumerate(value.split("\n")):
            head = f"{icon[level]} {pad(label, width)}" if i == 0 else " " * (width + 2)
            print(f"{head}{line}")
    print("─" * 64)
    if bad:
        print(f"{bad} 个问题需要处理，{warn} 个提醒。")
        return 1
    if warn:
        print(f"可用，但有 {warn} 个提醒。")
        return 0
    print("一切正常。")
    return 0


# ---------------------------------------------------------------- 命令：app / review（本地服务）

def cmd_review(args) -> int:
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    lock = threading.Lock()
    last = {"t": _time.time()}
    load()   # 提前建目录/校验格式
    if DATA_FILE.exists():
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(DATA_FILE, BACKUP_DIR / f"mistakes-{datetime.now():%Y%m%d-%H%M%S}-review.json")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *a):   # 静音
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _guard(self) -> bool:
            host = (self.headers.get("Host") or "").split(":")[0]
            return host in ("127.0.0.1", "localhost")

        def do_GET(self):
            last["t"] = _time.time()
            if not self._guard():
                return self._send(403, b"forbidden", "text/plain; charset=utf-8")
            path = self.path.split("?")[0]
            if path == "/__quit":
                self._send(200, b"bye", "text/plain; charset=utf-8")
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            if path == "/ping":
                return self._send(200, b"ok", "text/plain; charset=utf-8")
            if path not in ("/", "/index.html"):
                return self._send(404, b"not found", "text/plain; charset=utf-8")
            with lock:
                page = render_app(load(), week_monday(date.today()), interactive=True)
            return self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")

        def do_POST(self):
            if not self._guard() or self.path.split("?")[0] != "/api/mark":
                return self._send(403, b"forbidden", "text/plain; charset=utf-8")
            try:
                n = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(n) or b"{}")
                pid = str(payload.get("id", ""))
                field = payload.get("field")
                value = bool(payload.get("value"))
                with lock:
                    d = load()
                    p = find_problem(d, pid)
                    if p is None:
                        return self._send(404, b'{"ok":false}', "application/json")
                    if field == "star":
                        p["starred"] = value
                    elif field == "solved":
                        p["solved"] = value
                        p["solved_at"] = date.today().isoformat() if value else None
                    else:
                        return self._send(400, b'{"ok":false}', "application/json")
                    save(d, backup=False)
                log(f"app: {pid} {field}={value}")
                # 顺带刷新当周的静态报告，保证「页面」和「HTML」永远一致
                try:
                    wk = week_monday(date.today())
                    target = None
                    hb = LOG_DIR / "last-report.json"
                    if hb.exists():
                        try:
                            info = json.loads(hb.read_text(encoding="utf-8"))
                            if info.get("week"):
                                wk = parse_week(info["week"])
                            if info.get("path"):
                                target = Path(info["path"])
                        except Exception:
                            pass
                    write_report(d, wk, target or (REPORT_DIR / f"math-{week_key(wk)}.html"),
                                 touch_heartbeat=False)
                except Exception as exc:   # 报告失败不能影响这次点击
                    log(f"app: report refresh failed: {exc}")
                return self._send(200, b'{"ok":true}', "application/json")
            except Exception as exc:
                return self._send(500, json.dumps({"ok": False, "error": str(exc)}).encode(),
                                  "application/json")

    port = args.port
    # 同端口上有旧实例（可能还是旧代码）就让它退出，避免「改了代码界面不变」
    try:
        import urllib.request as _rq
        _rq.urlopen(f"http://127.0.0.1:{port}/__quit", timeout=1.5).read()
        print(f"检测到 {port} 上有旧实例，已请它退出。")
        _time.sleep(1.2)
    except Exception:
        pass
    for _ in range(10):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            port += 1
    else:
        raise SystemExit("找不到可用端口")

    if args.idle:
        def watchdog():
            while True:
                _time.sleep(10)
                if _time.time() - last["t"] > args.idle * 60:
                    print(f"\n空闲 {args.idle} 分钟，自动退出。")
                    server.shutdown()
                    return
        threading.Thread(target=watchdog, daemon=True).start()

    url = f"http://127.0.0.1:{port}/"
    print(f"错题页已启动：{url}")
    print("按 Ctrl+C 结束。数据直接写入 data/mistakes.json。")
    if args.idle:
        print(f"页面关掉 {args.idle} 分钟后自动退出。")
    if args.open:
        open_path(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        server.server_close()
    return 0


# ---------------------------------------------------------------- 入口

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cuoti.py", description="本地错题账本：记下卡在哪，按知识点算账。")
    p.add_argument("--version", action="version", version="cuoti.py 1.0")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="记一道/一批错题")
    a.add_argument("problem", nargs="?", help="题干")
    a.add_argument("--file", help="批量导入 JSON")
    a.add_argument("--topic", default="", help="模块，如 高数 / 线代 / 概率")
    a.add_argument("--point", action="append", default=[], help="知识点，可重复（第 1 个是主知识点）")
    a.add_argument("--kind", default="", help="错因：" + " / ".join(KINDS))
    a.add_argument("--stuck", default="", help="断在哪一步（最该写好的字段）")
    a.add_argument("--my-work", default="", help="当时的错解")
    a.add_argument("--answer", default="", help="正确答案")
    a.add_argument("--fix", default="", help="正确思路 / 下次先想到的那句话")
    a.add_argument("--source", default="", help="出处，如 2026-W47 660题 P42 T7")
    a.add_argument("--date", help="记于哪天，默认今天")
    a.add_argument("--star", action="store_true", help="顺便收藏")
    a.set_defaults(func=cmd_add)

    for name, fn, help_text in (("solve", cmd_solve, "搞懂了（别名 learn）"),
                                ("learn", cmd_solve, "solve 的别名"),
                                ("unsolve", cmd_unsolve, "又不会了，放回去"),
                                ("unlearn", cmd_unsolve, "unsolve 的别名"),
                                ("star", cmd_star, "收藏"),
                                ("unstar", cmd_unstar, "取消收藏")):
        s = sub.add_parser(name, help=help_text)
        s.add_argument("keys", nargs="+", help="id，或题干里的一段话")
        s.add_argument("--toggle", action="store_true", help="翻转当前状态")
        s.set_defaults(func=fn)

    s = sub.add_parser("list", help="列表")
    s.add_argument("--solved", action="store_true", help="只看搞懂的")
    s.add_argument("--starred", action="store_true", help="只看收藏")
    s.add_argument("--all", action="store_true", help="全部（含搞懂的）")
    s.add_argument("--point", help="按知识点过滤")
    s.add_argument("--topic", help="按模块过滤")
    s.add_argument("--kind", help="按错因过滤")
    s.add_argument("--week", help="某周新增，如 2026-W47")
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("points", help="按知识点统计（薄弱优先）")
    s.add_argument("--all", action="store_true", help="连已经搞懂的也算进来")
    s.set_defaults(func=cmd_points)

    sub.add_parser("stats", help="概览").set_defaults(func=cmd_stats)
    sub.add_parser("weeks", help="按周统计").set_defaults(func=cmd_weeks)

    s = sub.add_parser("app", help="起本地服务，遮挡着过一遍")
    s.add_argument("--port", type=int, default=8770)
    s.add_argument("--open", action="store_true", help="顺便打开浏览器")
    s.add_argument("--idle", type=int, default=30, help="空闲多少分钟自动退出，0 表示不退")
    s.set_defaults(func=cmd_review)
    r = sub.add_parser("review", help="app 的别名")
    r.add_argument("--port", type=int, default=8770)
    r.add_argument("--open", action="store_true")
    r.add_argument("--idle", type=int, default=30)
    r.set_defaults(func=cmd_review)

    s = sub.add_parser("report", help="生成周报")
    s.add_argument("--week", help="哪一周，默认本周（本周没数据就退回最近有数据的一周）")
    s.add_argument("--out", help="输出路径")
    s.add_argument("--title", help="标题，默认「第 N 周 · 错题报告」")
    s.add_argument("--max-review", type=int, default=80, help="「过一遍」最多放几道")
    s.add_argument("--open", action="store_true")
    s.set_defaults(func=cmd_report)

    s = sub.add_parser("weekly", help="补漏 + 出当周（幂等，调度用）")
    s.add_argument("--max-weeks", type=int, default=8)
    s.add_argument("--max-review", type=int, default=80)
    s.add_argument("--open", action="store_true")
    s.set_defaults(func=cmd_weekly)

    s = sub.add_parser("doctor", help="自检")
    s.add_argument("--task", help="顺带检查这个计划任务，如 MathMistakeWeekly")
    s.set_defaults(func=cmd_doctor)
    return p


def main(argv: list[str] | None = None) -> int:
    force_utf8()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except SystemExit:
        raise
    except FileNotFoundError as exc:
        print(f"✗ 找不到文件：{exc}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(f"✗ JSON 解析失败：{exc}（账本坏了？data/backup/ 里有旧版本）", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
