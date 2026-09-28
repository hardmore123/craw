#!/usr/bin/env python
"""skill 骨架健康自检。

改动 skill 或引擎白名单后跑一次，防止文档与代码脱节、防死链。
不联网、不抓站，纯静态核对。

用法：
    py -3.12 .kiro/skills/overseas-schema-gen/spec/selfcheck.py
退出码：0 全过；1 有问题（问题清单打印在最后）。
"""
from __future__ import annotations

import io
import json
import re
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent
ROOT = SKILL.parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

fails: list[str] = []

REQUIRED_FILES = [
    "SKILL.md", "probe.py", "selfcheck.py", "reference.md", "cases.jsonl",
    "task_template.md",
    "playbook/README.md", "playbook/static_lineup.md", "playbook/lazy_scroll.md",
    "playbook/numbered_paginate.md", "playbook/embedded_json.md",
    "playbook/needs_handwritten.md", "playbook/multilevel.md", "playbook/xhr_api.md",
]

SPEC_DOC = ROOT / "docs" / "AdapterSpec_schema规范.md"


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
        # 找全率与 verdict 一致性：标 success 就不该覆盖率明显偏低
        cov = str((d.get("result") or {}).get("coverage") or "")
        if d.get("verdict") == "success":
            m = re.match(r"(\d+)", cov)
            if m and int(m.group(1)) < 95:
                print(f"    line{i}({d.get('code')}): verdict=success 但 coverage={cov}")
                fails.append(f"{d.get('code')} 标 success 但找全率 {cov} < 95%")
    print(f"    共 {n} 行，全部可解析")


def check_deadlinks() -> None:
    print("[4] 文档交叉引用（防死链）")
    bad = 0
    docs = list(SKILL.glob("*.md")) + list((SKILL / "playbook").glob("*.md"))
    for md in docs:
        text = md.read_text(encoding="utf-8")
        for ref in set(re.findall(r"(?:playbook/)?([a-z_]+\.md)", text)):
            # 跳过模板占位符：<code>_task.md 的残段、______.md 这类填空位
            if ref.startswith("_") or set(ref[:-3]) <= {"_"}:
                continue
            if (SKILL / "playbook" / ref).exists() or (SKILL / ref).exists():
                continue
            print(f"    死链: {md.name} -> {ref}")
            fails.append(f"死链 {md.name} -> {ref}")
            bad += 1
    print(f"    检查 {len(docs)} 个文档，死链 {bad}")


def check_whitelist_sync() -> None:
    """★核心检查：引擎白名单常量是否与 schema 规范文档一致。

    这是最容易脱节的地方——有人扩了引擎却忘了改文档，
    智能体照旧文档写 schema 就会踩空。
    """
    print("[5] 引擎白名单 与 schema 规范文档 是否同步")
    try:
        from overseas.spec_generator import (KNOWN_SNIPPETS,
                                             SUPPORTED_DISCOVER_MODES,
                                             SUPPORTED_EXTRACT_METHODS)
    except Exception as e:
        print("    无法导入 spec_generator:", e)
        fails.append(f"无法导入 spec_generator: {e}")
        return

    if not SPEC_DOC.exists():
        print("    规范文档不存在:", SPEC_DOC)
        fails.append("AdapterSpec_schema规范.md 不存在")
        return
    doc = SPEC_DOC.read_text(encoding="utf-8")

    groups = [
        ("discover.mode", SUPPORTED_DISCOVER_MODES),
        ("snippet_ref", KNOWN_SNIPPETS),
        ("extract_order", SUPPORTED_EXTRACT_METHODS),
    ]
    for label, values in groups:
        for v in sorted(values):
            if v in doc:
                continue
            print(f"    代码有 {label}={v} 但规范文档未提及")
            fails.append(f"规范文档缺 {label}={v}（代码已支持，文档需更新）")
        print(f"    {label}: 代码 {len(values)} 个 -> 文档均已覆盖"
              if all(v in doc for v in values) else f"    {label}: 有脱节")

    # 反向：文档里若把某值写成"仅/唯一"，而代码已扩展，需提醒
    m = re.search(r"discover\.mode\s*:\s*([a-z_,\s]+)", doc)
    if m and len(SUPPORTED_DISCOVER_MODES) > len(
            [x for x in m.group(1).split(",") if x.strip()]):
        print("    ⚠ 代码的 discover.mode 数量多于文档能力边界一览，请核对 §6")
        fails.append("规范文档 §6 能力边界一览 与代码 discover.mode 数量不符")


def check_spec_examples() -> None:
    """规范文档里的 schema 示例必须真能过 validate_spec。"""
    print("[6] 规范文档示例可用性")
    try:
        from overseas.spec_generator import validate_spec
    except Exception as e:
        print("    无法导入 validate_spec:", e)
        fails.append(f"无法导入 validate_spec: {e}")
        return
    if not SPEC_DOC.exists():
        return
    doc = SPEC_DOC.read_text(encoding="utf-8")
    blocks = re.findall(r"```json\n(.*?)\n```", doc, re.S)
    checked = 0
    for i, b in enumerate(blocks, 1):
        try:
            spec = json.loads(b)
        except Exception:
            continue  # 片段示例（如只有 spec 段）不是完整 JSON，跳过
        if "discover" not in spec:
            continue
        checked += 1
        problems = validate_spec(spec, None)
        print(f"    块{i}({spec.get('code')}):",
              "PASS" if not problems else "FAIL")
        for p in problems:
            print("        -", p)
        if problems:
            fails.append(f"规范文档示例 {spec.get('code')} 未过 validate_spec")
    print(f"    完整示例 {checked} 个")


def check_real_specs() -> None:
    """docs/specs/ 下的已转正 schema 必须全部合法。"""
    print("[7] 已转正 schema 回归")
    try:
        from overseas.spec_generator import validate_spec
    except Exception:
        return
    spec_dir = ROOT / "docs" / "specs"
    if not spec_dir.exists():
        print("    无 docs/specs/ 目录，跳过")
        return
    files = sorted(spec_dir.glob("*.spec.json"))
    for f in files:
        try:
            spec = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"    {f.name}: JSON 解析失败 {e}")
            fails.append(f"{f.name} JSON 解析失败")
            continue
        problems = validate_spec(spec, None)
        print(f"    {f.name}:", "PASS" if not problems else "FAIL")
        for p in problems:
            print("        -", p)
        if problems:
            fails.append(f"{f.name} 未过 validate_spec")
    print(f"    共 {len(files)} 份")


def main() -> int:
    print("=" * 56)
    print("spec-schema-gen skill 骨架自检")
    print("=" * 56)
    check_files()
    check_frontmatter()
    check_cases()
    check_deadlinks()
    check_whitelist_sync()
    check_spec_examples()
    check_real_specs()
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
