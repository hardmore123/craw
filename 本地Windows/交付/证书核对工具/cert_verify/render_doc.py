"""把 介绍文档.Rmd 渲染成 HTML。

优先级：
1. 有 R + rmarkdown  → Rscript -e 'rmarkdown::render(...)'，标准 knit 流程
2. 只有 pandoc       → 先把 knitr 语法（```{r ...}、内联 `r ...`、::: 块）
                        规范成普通 Markdown，再交 pandoc
3. 都没有            → 打印安装提示，Rmd 本身仍可当 Markdown 直接看

    python -m cert_verify.render_doc
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
RMD = os.path.join(HERE, "介绍文档.Rmd")


def _out_path() -> str:
    # 输出到包根目录，方便双击
    return os.path.join(os.path.dirname(HERE), "介绍文档.html")


# ---------- 路线 1：R ----------
def render_with_r(rmd: str, out: str) -> bool:
    rscript = shutil.which("Rscript")
    if not rscript:
        return False
    code = (f'if (!requireNamespace("rmarkdown", quietly=TRUE)) quit(status=2); '
            f'rmarkdown::render("{rmd.replace(chr(92), "/")}", '
            f'output_file="{os.path.basename(out)}", '
            f'output_dir="{os.path.dirname(out).replace(chr(92), "/")}")')
    print("[1/3] 检测到 R，走 rmarkdown::render …")
    r = subprocess.run([rscript, "-e", code], capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if r.returncode == 0 and os.path.exists(out):
        return True
    if r.returncode == 2:
        print("      R 在，但没装 rmarkdown 包：install.packages('rmarkdown')")
    else:
        print(f"      R 渲染失败（退出码 {r.returncode}），转 pandoc")
        if r.stderr:
            print("      " + r.stderr.strip()[:300])
    return False


# ---------- 路线 2：pandoc ----------
def rmd_to_markdown(text: str) -> str:
    """把 knitr 专有语法降级成普通 Markdown，好让 pandoc 直接吃。"""
    # YAML 头里 rmarkdown 专有的 output 段，pandoc 不认，换成 pandoc 的写法
    text = re.sub(
        r"^---\n.*?\n---\n",
        lambda m: _fix_yaml(m.group(0)),
        text,
        count=1,
        flags=re.S,
    )
    # 内联 R 代码 `r format(Sys.Date(), ...)` → 直接给日期
    import datetime
    today = datetime.date.today().isoformat()
    text = re.sub(r"`r\s+format\(Sys\.Date\(\)[^`]*\)`", today, text)
    text = re.sub(r"`r\s+[^`]+`", "", text)
    # setup 块不显示
    text = re.sub(r"```\{r setup[^}]*\}.*?```\n", "", text, flags=re.S)
    # ```{r xxx} → ```r ；```{python} → ```python
    text = re.sub(r"```\{(\w+)[^}]*\}", lambda m: "```" + m.group(1), text)
    # pandoc fenced div ::: {.alert} → 保留为 blockquote，样式更通用
    text = re.sub(r":::\s*\{\.alert\}\s*\n(.*?)\n:::",
                  lambda m: "\n".join("> " + ln for ln in m.group(1).split("\n")),
                  text, flags=re.S)
    return text


def _fix_yaml(yaml_block: str) -> str:
    title = re.search(r'^title:\s*"?(.*?)"?\s*$', yaml_block, re.M)
    sub = re.search(r'^subtitle:\s*"?(.*?)"?\s*$', yaml_block, re.M)
    t = title.group(1) if title else "文档"
    if sub:
        t += " — " + sub.group(1)
    import datetime
    return f'---\ntitle: "{t}"\ndate: "{datetime.date.today():%Y-%m-%d}"\nlang: zh-CN\n---\n'


CSS = """
body{font-family:"Microsoft YaHei","Segoe UI",Arial,sans-serif;max-width:900px;
margin:2rem auto;padding:0 1.2rem;line-height:1.75;color:#24292f;}
h1{border-bottom:2px solid #d0d7de;padding-bottom:.3em;}
h2{border-bottom:1px solid #d8dee4;padding-bottom:.25em;margin-top:2em;}
code{background:#f6f8fa;padding:.15em .35em;border-radius:4px;font-size:92%;}
pre{background:#f6f8fa;padding:1em;border-radius:6px;overflow-x:auto;}
pre code{background:none;padding:0;}
table{border-collapse:collapse;width:100%;margin:1em 0;}
th,td{border:1px solid #d0d7de;padding:.5em .7em;text-align:left;vertical-align:top;}
th{background:#f6f8fa;}
tr:nth-child(even){background:#fbfcfd;}
blockquote{border-left:4px solid #0969da;background:#f0f6fc;margin:1em 0;
padding:.6em 1em;color:#24292f;}
#TOC{background:#f6f8fa;border:1px solid #d0d7de;border-radius:6px;padding:1em 1.5em;}
"""


def render_with_pandoc(rmd: str, out: str) -> bool:
    pandoc = shutil.which("pandoc")
    if not pandoc:
        return False
    print("[2/3] 没有 R，改用 pandoc 渲染 …")
    with open(rmd, "r", encoding="utf-8") as f:
        md = rmd_to_markdown(f.read())

    tmp_md = os.path.join(HERE, "_doc_tmp.md")
    tmp_css = os.path.join(HERE, "_doc_tmp.css")
    with open(tmp_md, "w", encoding="utf-8") as f:
        f.write(md)
    with open(tmp_css, "w", encoding="utf-8") as f:
        f.write(CSS)
    try:
        r = subprocess.run(
            [pandoc, tmp_md, "-o", out, "--standalone", "--toc", "--toc-depth=3",
             "--metadata", "lang=zh-CN", "--css", os.path.basename(tmp_css),
             "--embed-resources", "--number-sections",
             "-f", "markdown+pipe_tables+fenced_code_attributes"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=HERE)
        if r.returncode != 0:
            print("      pandoc 失败：" + (r.stderr or "")[:400])
            return False
        return os.path.exists(out)
    finally:
        for p in (tmp_md, tmp_css):
            if os.path.exists(p):
                os.remove(p)


def main() -> int:
    if not os.path.exists(RMD):
        print(f"找不到 {RMD}")
        return 1
    out = _out_path()

    if render_with_r(RMD, out) or render_with_pandoc(RMD, out):
        print(f"\n渲染完成: {out}")
        print("双击即可在浏览器打开。")
        return 0

    print("[3/3] 既没有 R 也没有 pandoc，无法生成 HTML。")
    print("    装 R + rmarkdown：https://cran.r-project.org/  然后 install.packages('rmarkdown')")
    print("    或只装 pandoc：  https://pandoc.org/installing.html")
    print(f"\n不装也行 —— {os.path.basename(RMD)} 本身就是 Markdown，用记事本/VS Code 直接看。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
