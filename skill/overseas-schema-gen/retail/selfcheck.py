#!/usr/bin/env python
"""retail-schema-gen skill 骨架健康自检。

改动 skill 或零售引擎白名单后跑一次，防止文档与代码脱节、防死链。
不联网、不抓站，纯静态核对。

用法：
    py -3.12 .kiro/skills/overseas-schema-gen/retail/selfcheck.py
退出码：0 全过；1 有问题（问题清单打印在最后）。
"""
from __future__ import annotations

import io
import json
import re
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent
# SKILL 是目录（retail-schema-gen/）；项目根在 it 的 parents[3]（含 overseas/ 与 docs/）
ROOT = SKILL.parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

fails: list[str] = []

REQUIRED_FILES = [
    "SKILL.md", "probe.py", "selfcheck.py", "reference.md", "cases.jsonl",
    "task_template.md",
    "playbook/README.md", "playbook/single_shop.md", "playbook/multi_shop.md",
    "playbook/landed_pdp.md", "playbook/reviews_page.md", "playbook/needs_human.md",
]

SPEC_DOC = ROOT / "docs" / "RetailSpec_schema规范.md"


def check_files() -> None:
    print("[1] 文件完整性")
    missing = 0
    for rel in REQUIRED_FILES:
        p = SKILL / rel
        if not p.exists() or p.stat().st_size == 0:
            print("    MISSING/EMPTY:", rel)
            fails.append(f"缺文件 {rel}")
            missing += 1
    print(f"    必需 {len(REQUIRED_FILES)} 个，缺失 {missing}")


def check_frontmatter() -> None:
    print("[2] SKILL.md frontmatter")
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    ok = text.startswith("---") and "name:" in text and "description:" in text
    print("    ->", "PASS" if ok else "FAIL")
    if not ok:
        fails.append("SKILL.md 缺 frontmatter（name/description）")


def check_cases() -> None:
    print("[3] cases.jsonl 可解析性与字段完整性")
    n = 0
    required = {"code", "verdict", "signals", "result", "lesson"}
    for i, line in enumerate(io.open(SKILL / "cases.jsonl", encoding="utf-8"), 1):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception as e:
            print(f"    line{i}: PARSE FAIL {e}")
            fails.append(f"cases.jsonl 第 {i} 行无法解析")
            continue
        n += 1
        if "_schema" in d:
            continue
        miss = required - set(d)
        if miss:
            print(f"    line{i}({d.get('code')}): 缺字段 {sorted(miss)}")
            fails.append(f"cases.jsonl {d.get('code')} 缺字段 {sorted(miss)}")
    print(f"    共 {n} 行，全部可解析")


def check_deadlinks() -> None:
    print("[4] 文档交叉引用（防死链）")
    bad = 0
    docs = list(SKILL.glob("*.md")) + list((SKILL / "playbook").glob("*.md"))
    for md in docs:
        text = md.read_text(encoding="utf-8")
        for ref in set(re.findall(r"(?:playbook/)?([a-z_]+\.md)", text)):
            if ref.startswith("_") or set(ref[:-3]) <= {"_"}:
                continue
            if (SKILL / "playbook" / ref).exists() or (SKILL / ref).exists():
                continue
            print(f"    死链: {md.name} -> {ref}")
            fails.append(f"死链 {md.name} -> {ref}")
            bad += 1
    print(f"    检查 {len(docs)} 个文档，死链 {bad}")


def check_whitelist_sync() -> None:
    """★核心检查：零售线白名单常量是否与 schema 规范文档一致。"""
    print("[5] 引擎白名单 与 schema 规范文档 是否同步")
    try:
        from overseas.retail_spec import (SUPPORTED_MATCH_STRATEGIES,
                                          SUPPORTED_REVIEW_PAGINATE,
                                          SUPPORTED_SHOP_MODES)
    except Exception as e:
        print("    无法导入 retail_spec:", e)
        fails.append(f"无法导入 retail_spec: {e}")
        return

    if not SPEC_DOC.exists():
        print("    规范文档不存在:", SPEC_DOC)
        fails.append("RetailSpec_schema规范.md 不存在")
        return
    doc = SPEC_DOC.read_text(encoding="utf-8")

    groups = [
        ("match.strategy", SUPPORTED_MATCH_STRATEGIES),
        ("paginate.reviews.type", SUPPORTED_REVIEW_PAGINATE),
        ("shops.mode", SUPPORTED_SHOP_MODES),
    ]
    for label, values in groups:
        pd = [v for v in values if v in doc]
        if len(pd) != len(values):
            missing = [v for v in values if v not in doc]
            print(f"    代码有 {label}={missing} 但规范文档未提及")
            fails.append(f"规范文档缺 {label}={missing}")
        print(f"    {label}: 代码 {len(values)} 个 -> 文档覆盖 {len(pd)}")


def check_retail_specs_validate() -> None:
    """docs/retail_specs/ 下的真实 spec 必须全部合法。"""
    print("[6] 已转正零售 spec 回归")
    try:
        from overseas.retail_spec import load_retail_spec, validate_retail_spec
    except Exception as e:
        print("    无法导入 retail_spec:", e)
        return
    spec_dir = ROOT / "docs" / "retail_specs"
    if not spec_dir.exists():
        print("    无 docs/retail_specs/ 目录，跳过")
        return
    files = sorted(spec_dir.glob("*.retail.json"))
    for f in files:
        try:
            spec = load_retail_spec(f)
        except Exception as e:
            print(f"    {f.name}: 加载失败 {e}")
            fails.append(f"{f.name} 加载失败")
            continue
        problems = validate_retail_spec(spec)
        print(f"    {f.name}:", "PASS" if not problems else "FAIL")
        for p in problems:
            print("        -", p)
        if problems:
            fails.append(f"{f.name} 未过 validate_retail_spec")
    print(f"    共 {len(files)} 份")


def main() -> int:
    print("=" * 56)
    print("retail-schema-gen skill 骨架自检")
    print("=" * 56)
    check_files()
    check_frontmatter()
    check_cases()
    check_deadlinks()
    check_whitelist_sync()
    check_retail_specs_validate()
    print("\n" + "=" * 56)
    if fails:
        print(f"自检失败 {len(fails)} 项：")
        for f in fails:
            print("  -", f)
        return 1
    print("全部自检通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())