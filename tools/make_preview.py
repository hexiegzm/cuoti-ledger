"""生成 preview/ 里的截图（只在演示数据上跑，不碰真账本）。

用法：python tools/make_preview.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="cuoti-preview-"))
os.environ["CUOTI_DATA"] = str(TMP / "data")
os.environ["CUOTI_REPORTS"] = str(TMP / "reports")
os.environ["CUOTI_LOGS"] = str(TMP / "logs")

import cuoti  # noqa: E402

CHROME_CANDIDATES = (
    os.environ.get("CHROME"),
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/google-chrome",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)
HTML = TMP / "report.html"


def find_chrome() -> Path:
    for cand in CHROME_CANDIDATES:
        if cand and Path(cand).exists():
            return Path(cand)
    raise SystemExit("找不到 Chrome / Edge：用环境变量 CHROME 指定可执行文件路径")


def shot(src: Path, out: Path, width: int, height: int) -> None:
    subprocess.run([str(find_chrome()), "--headless=new", "--disable-gpu", "--hide-scrollbars",
                    "--force-device-scale-factor=1", f"--window-size={width},{height}",
                    f"--screenshot={out}", "file:///" + str(src).replace("\\", "/")],
                   check=True, capture_output=True)


def variant(tab: str, extra_js: str = "") -> Path:
    html = HTML.read_text(encoding="utf-8")
    html = html.replace(
        "const initial = new URLSearchParams(location.search).get('tab');",
        f"const initial = '{tab}';")
    if extra_js:
        html = html.replace("else filter();", "else filter();\n" + extra_js)
    path = TMP / f"v-{tab}.html"
    path.write_text(html, encoding="utf-8")
    return path


def main() -> int:
    cuoti.force_utf8()
    data = cuoti.load()
    sample = json.loads((ROOT / "examples" / "sample.json").read_text(encoding="utf-8"))
    for rec in sample["problems"]:
        cuoti.upsert(data, rec)
    cuoti.save(data)
    monday = date(2026, 11, 16)
    cuoti.write_report(data, monday, HTML)

    out = ROOT / "preview"
    out.mkdir(exist_ok=True)
    # 可点版本（带按钮）：直接用交互模式渲染，再把第一张卡翻开
    app_html = cuoti.render_app(data, monday, interactive=True)
    (TMP / "app.html").write_text(
        app_html.replace("else filter();",
                         "else filter();\n"
                         "const c0=document.querySelector('.panel.on .card'); if(c0) c0.classList.add('reveal');"),
        encoding="utf-8")
    shot(TMP / "app.html", out / "两个按钮.png", 900, 620)
    shot(variant("study"), out / "过一遍.png", 900, 1500)
    shot(variant("weak"), out / "薄弱知识点.png", 900, 1300)

    pdf = TMP / "print.pdf"
    subprocess.run([str(find_chrome()), "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={pdf}", "file:///" + str(HTML).replace("\\", "/")],
                   check=True, capture_output=True)
    pdftoppm = shutil.which("pdftoppm")
    if not pdftoppm:
        raise SystemExit("需要 pdftoppm（poppler 或 MiKTeX）把 PDF 转成 PNG")
    subprocess.run([pdftoppm, "-r", "110", "-png", "-f", "1", "-l", "1", str(pdf), str(TMP / "p1")],
                   check=True, capture_output=True)
    subprocess.run([pdftoppm, "-r", "110", "-png", "-f", "3", "-l", "3", str(pdf), str(TMP / "p3")],
                   check=True, capture_output=True)
    for src, dst in ((TMP / "p1-1.png", out / "打印-正面.png"), (TMP / "p3-3.png", out / "打印-答案.png")):
        if src.exists():
            shutil.copy2(src, dst)

    print(f"✓ {out}")
    for p in sorted(out.iterdir()):
        print(f"  {p.name}  {p.stat().st_size // 1024} KB")
    shutil.rmtree(TMP, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
