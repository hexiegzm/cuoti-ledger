# cuoti-ledger · a local mistake ledger for math

A zero-dependency, offline, plain-text ledger for the problems you got wrong:
**record where the reasoning broke → redo them behind a blur → mark them done → get a printable worksheet every Sunday.**

Everything lives in one hand-editable JSON file. No server, no account, no telemetry. Python 3 standard library only.

> The Chinese README ([README.md](README.md)) is the primary document; this is a summary for readers who don't read Chinese.

---

## Why not just a notebook of wrong answers

For vocabulary, a word is a reusable unit: learn it and you can use it next time.

Math is different. **You will never see that exact problem again**, so "copy the problem down and redo it later" mostly teaches you *that* answer, not *that class* of solution. What actually repeats is the **breakpoint**:

```
11-18  used an equivalent-infinitesimal substitution across a `+`, lost the x³ term
11-19  same trap: never thought of x²/2 for 1 − cos x
11-21  didn't spot that a radical difference is a first-order term
```

Three different problems, one breakpoint.

So the ledger has two layers:

- the **record** is a problem (statement, your wrong work, where it broke, answer, the one line to remember);
- the **unit of accounting and review is a knowledge point** (`points`, 1–3 per problem).

The weekly report therefore has a **weak knowledge points** view: problems sharing a breakpoint are grouped together, and a point you've broken three times floats to the top. "Which knowledge point have I failed three times" is the only thing that makes keeping mistakes worth it.

A second difference: "understood the solution" is not "can do it". So the review page hides the *answer*, not the problem — and the printed sheet is a **worksheet** (statement + a line to write your answer) with the **answer key on the next page**.

## Install

```bash
git clone https://github.com/hexiegzm/cuoti-ledger.git
cd cuoti-ledger
python cuoti.py add --file examples/sample.json     # try it with 12 sample problems
python cuoti.py report --open                       # render the weekly report
```

Windows users can double-click `错题本.cmd` to open the review page.

## Commands

```bash
python cuoti.py add "求 lim(x→0) (tan x − sin x)/x³" --topic 极限 \
  --point 等价无穷小 --kind 方法 \
  --stuck "在加减法里直接做等价替换，把三阶项丢了" \
  --answer "1/2" --fix "先通分成 tan x(1−cos x)，或泰勒展开到 x³"

python cuoti.py solve 20261123-001     # done (alias: learn)
python cuoti.py unsolve 20261123-001   # forgot it again
python cuoti.py star 20261123-001      # keep it in the favourites
python cuoti.py points                 # which knowledge point failed 3 times
python cuoti.py list                   # still open
python cuoti.py app --open             # review page (masked cards, writes the file directly)
python cuoti.py report --open          # weekly report
python cuoti.py weekly                 # idempotent: backfill + current week
python cuoti.py doctor --task MathMistakeWeekly
```

## Two states, no scores

```
   add              star
  ∅ ───► open ◄──────────────► ★ starred
          │  ▲                 │
   solve  │  │ unsolve         │   (independent)
          ▼  │                 │
       solved ─────────────────┘
```

No mastery score, no spaced-repetition schedule, no "times wrong" counter. There is no quiz loop to feed such a number, so any score would sit at its initial value forever. The judgement is yours; the script only records and renders.

## Files

```
cuoti.py              single-file program (stdlib only)
SPEC.md               the protocol: fields, states, statistics, rendering & print contract
AGENTS.md             conventions for the AI that grades your homework
README.md             Chinese README
examples/sample.json  12 sample problems
preview/              screenshots
tests/                49 unittest cases
```

Your data goes to `data/mistakes.json` (+ automatic snapshots in `data/backup/`), reports to `reports/`, logs to `logs/` — all git-ignored.

## Weekly schedule

| Task | When | What |
|---|---|---|
| `MathMistakeWeekly` | Sunday 20:10 | generate the current week's report |
| `MathMistakeCatchup` | daily 12:05 | backfill weeks missed while the machine was off |

Both run the same idempotent `cuoti.py weekly`.

## Writing the problem text

The report is a single offline HTML file — no KaTeX, no CDN. Write math in plain text with Unicode: `x²`, `√2`, `≤`, `∫₀¹`, `∑(n=1..∞)`, `α θ π`. `cuoti.py add` warns when it smells LaTeX (`\frac`, `$`).

## License

MIT
