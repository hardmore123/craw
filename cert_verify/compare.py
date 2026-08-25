"""字段归一化 + 一致性比对。

比对的难点不在抓取，在"同一个意思写法不同"。这里做三层处理：
1. 归一化：全角半角、大小写、空格、标点、公司后缀（Co.,Ltd. / CO LTD）、标准号写法。
2. 分字段策略：
   - 制造商/生产厂：归一化后做包含 + 相似度
   - 型号：切分成型号集合（表格常写 "A/B/C" 或 "A、B"），判子集
   - 标准：抽取成标准号集合（EN IEC 62368-1:2020+A11 -> EN62368-1:2020），判交集
   - 状态：映射到 有效/失效 两态
3. 网站没有该字段（如 Certipedia 不公开生产厂）→ 记"网站无此项"，不判不一致。
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass

MATCH = "一致"
MISMATCH = "不一致"
MISSING_SITE = "网站无此项"
MISSING_SHEET = "表格未填"

FIELD_LABELS = {
    "manufacturer": "制造商",
    "factory": "生产厂",
    "model": "型号",
    "standard": "标准",
    "cert_status": "证书状态",
}

# 公司名里可忽略的后缀/词
_CORP_NOISE = re.compile(
    r"\b(co|company|companies|corp|corporation|inc|ltd|limited|gmbh|kg|ag|sa|srl|bv|nv|"
    r"plc|llc|lp|pte|sdn|bhd|s?\.?a\.?r\.?l|kabushiki|kaisha|electronics?|electric|"
    r"technology|technologies|industrial|industries|international|group|holdings?)\b",
    re.I,
)

_STD_RE = re.compile(
    r"\b((?:EN\s*IEC|EN|IEC|UL|CSA|AS/NZS|GB|JIS|DIN|VDE|ANSI|CAN/CSA)\s*"
    r"[\d]{3,5}(?:[-–][\d]+)*(?:[-–][\d]+)?(?:\s*:\s*\d{4})?(?:\s*\+\s*A\d+\w*)*)",
    re.I,
)

_VALID_WORDS = ("valid", "active", "有效", "在有效期", "gültig", "current", "listed")
_INVALID_WORDS = ("withdraw", "expired", "cancel", "revok", "suspend", "invalid",
                  "失效", "撤销", "注销", "过期", "abgelaufen", "zurückgezogen")


# ---------------- 归一化 ----------------

def norm_text(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    s = s.replace("\u00a0", " ")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def norm_key(s: str) -> str:
    """用于相等判断的强归一化：只留字母数字。"""
    s = norm_text(s).lower()
    s = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", s)
    return s


def norm_company(s: str) -> str:
    s = norm_text(s).lower()
    s = re.sub(r"[^\w\s\u4e00-\u9fff]+", " ", s)
    s = _CORP_NOISE.sub(" ", s)
    return re.sub(r"\s+", "", s)


# VDE 的 typeNames 用 "bis/to" 表示型号范围、用 * 表示通配后缀
_RANGE_WORDS = re.compile(r"(?:\bbis\s*/\s*to\b|\bbis\b|\bto\b)", re.I)
_NOISE_TOKEN = re.compile(
    r"^(refer|to|accompanying|certificates?|additionalsheets?|等|series|系列|见附页)+$")


def _strip_model_noise(s: str) -> str:
    s = norm_text(s)
    # 先去掉括号里的说明（品牌、备注），再切分 —— 顺序反了会把括号内容并进型号
    s = re.sub(r"[\(（][^\)）]*[\)）]", " ", s)
    s = re.sub(r"[\(（][^\)）]*$", " ", s)          # 未闭合的括号
    s = _RANGE_WORDS.sub(" ", s)
    # 去掉常见修饰词（series/系列），避免 "UDA P series" vs "UDA-P" 误判
    s = re.sub(r"\b(series|系列)\b", " ", s, flags=re.I)
    return s


def split_models(s: str) -> set[str]:
    """型号列常见写法：'A24-065N3A/A24-065N3B'、'A、B，C'、'A; B'、换行。"""
    parts = re.split(r"[\/,;、，；\n\r\|]+|\s{2,}", _strip_model_noise(s))
    out = set()
    for p in parts:
        p = p.strip(" *()（）[]")
        k = norm_key(p)
        if k and not _NOISE_TOKEN.fullmatch(k):
            out.add(k)
    return out


def model_globs(s: str) -> list[re.Pattern]:
    """把 'WMR05D561K *' 这类带通配的型号写法编译成正则。

    VDE 的型号列表大量使用 '<前缀> *' 表示同系列后缀任意，
    直接做字符串相等会全判不一致。
    """
    pats: list[re.Pattern] = []
    for p in re.split(r"[\/,;、，；\n\r\|]+", _strip_model_noise(s)):
        if "*" not in p:
            continue
        # 只保留字母数字和 *，其余（空格、连字符）去掉，与 norm_key 对齐
        g = re.sub(r"[^0-9a-z\u4e00-\u9fff*]+", "", p.lower())
        stem = g.replace("*", "")
        if len(stem) < 3:          # 防止 '*' 之类退化成匹配一切
            continue
        pats.append(re.compile("^" + ".*".join(re.escape(x) for x in g.split("*")) + "$"))
    return pats


def split_standards(s: str) -> set[str]:
    """抽标准号集合。'EN IEC 62368-1:2020+A11' -> 'en62368-1:2020+a11'
    同时保留一个不带年份/修订的粗粒度键，便于宽松比对。"""
    s = norm_text(s)
    out = set()
    for m in _STD_RE.finditer(s):
        raw = re.sub(r"\s+", "", m.group(1)).lower().replace("–", "-")
        raw = raw.replace("eniec", "en")           # EN IEC 62368-1 == EN 62368-1
        out.add(raw)
    return out


def std_core(std: str) -> str:
    """去年份、去修订，只留 '标准体系+编号'，如 en62368-1。"""
    return re.sub(r"(:\d{4}.*|\+a\d+\w*)$", "", std)


def norm_status(s: str) -> str:
    low = norm_text(s).lower()
    if not low:
        return ""
    if any(w in low for w in _INVALID_WORDS):
        return "失效"
    if any(w in low for w in _VALID_WORDS):
        return "有效"
    return low


def ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


# ---------------- 单字段比对 ----------------

def cmp_company(sheet: str, site: str) -> tuple[str, str]:
    a, b = norm_company(sheet), norm_company(site)
    if not a:
        return MISSING_SHEET, ""
    if not b:
        return MISSING_SITE, ""
    if a == b or a in b or b in a:
        return MATCH, ""
    r = ratio(a, b)
    if r >= 0.86:
        return MATCH, f"近似({r:.2f})"
    return MISMATCH, f"表格={norm_text(sheet)} | 网站={norm_text(site)}"


def cmp_model(sheet: str, site: str) -> tuple[str, str]:
    a, b = split_models(sheet), split_models(site)
    if not a:
        return MISSING_SHEET, ""
    if not b and "*" not in (site or ""):
        return MISSING_SITE, ""
    if a & b:
        missing = a - b
        if missing:
            return MATCH, f"部分命中，网站未列: {len(missing)}个"
        return MATCH, ""
    # VDE 式通配：网站写 'WMR05D561K *'，表格写具体型号
    globs = model_globs(site)
    if globs:
        hit = [x for x in a if any(p.match(x) for p in globs)]
        if hit:
            return MATCH, "通配匹配" + ("" if len(hit) == len(a) else f"，{len(a) - len(hit)}个未命中")
    # 型号常有后缀差异，做逐对相似度
    if any(ratio(x, y) >= 0.9 for x in a for y in b):
        return MATCH, "近似"
    return MISMATCH, f"表格={norm_text(sheet)} | 网站={_short(site)}"


def _short(s: str, n: int = 120) -> str:
    s = norm_text(s)
    return s if len(s) <= n else s[:n] + "…"


def cmp_standard(sheet: str, site: str) -> tuple[str, str]:
    a, b = split_standards(sheet), split_standards(site)
    if not a:
        return MISSING_SHEET, ""
    if not b:
        return MISSING_SITE, ""
    if a & b:
        return MATCH, ""
    ac = {std_core(x) for x in a}
    bc = {std_core(x) for x in b}
    if ac & bc:
        return MATCH, f"版本/年份不同: 表格={sorted(a)} 网站={sorted(b)}"
    return MISMATCH, f"表格={sorted(a) or norm_text(sheet)} | 网站={sorted(b)}"


def cmp_status(sheet: str, site: str) -> tuple[str, str]:
    a, b = norm_status(sheet), norm_status(site)
    if not a:
        return MISSING_SHEET, f"网站={norm_text(site)}"
    if not b:
        return MISSING_SITE, ""
    if a == b:
        return MATCH, ""
    return MISMATCH, f"表格={norm_text(sheet)} | 网站={norm_text(site)}"


_CMP = {
    "manufacturer": cmp_company,
    "factory": cmp_company,
    "model": cmp_model,
    "standard": cmp_standard,
    "cert_status": cmp_status,
}


@dataclass
class FieldVerdict:
    """单字段比对结果。site_value 为空但 verdict=MISSING_SITE 表示网站无此项。"""

    field: str            # manufacturer / factory / model / standard / cert_status
    label: str            # 中文标签
    sheet_value: str      # 表格原值
    site_value: str       # 官网值
    verdict: str          # MATCH / MISMATCH / MISSING_SITE / MISSING_SHEET
    detail: str = ""      # 补充说明（近似度、版本差异、通配命中等）


def compare_fields(sheet_row: dict, rec) -> list[FieldVerdict]:
    """对五个字段逐一比对，返回字段级结论列表。

    只在 rec.status == FOUND 时产出字段结论；非 found 状态返回空列表
    （整行结论由 compare_record 按状态生成）。这是 Web 展示、M 列文案、
    Excel 标红三者共同的唯一判断来源。
    """
    from .models import FOUND

    if rec.status != FOUND:
        return []

    out: list[FieldVerdict] = []
    for field, label in FIELD_LABELS.items():
        sheet_val = sheet_row.get(field, "") or ""
        site_val = getattr(rec, field, "") or ""
        verdict, detail = _CMP[field](sheet_val, site_val)
        out.append(FieldVerdict(field=field, label=label,
                                sheet_value=sheet_val, site_value=site_val,
                                verdict=verdict, detail=detail))
    return out


def summarize_fields(fields: list[FieldVerdict]) -> tuple[bool, str]:
    """把字段级结论组装成 (是否全部一致, M 列摘要文本)。

    不一致字段附官网正确值，便于人工直接看到应改成什么。
    """
    diffs, notes, ok_fields = [], [], []
    for fv in fields:
        if fv.verdict == MATCH:
            ok_fields.append(fv.label + (f"({fv.detail})" if fv.detail else ""))
        elif fv.verdict == MISMATCH:
            # detail 形如 "表格=X | 网站=Y"，本身已含官网正确值
            diffs.append(f"{fv.label}不一致[{fv.detail}]")
        else:
            notes.append(f"{fv.label}{fv.verdict}")

    if diffs:
        head = "✗ 不一致：" + "；".join(diffs)
        if ok_fields:                       # 不一致时也列出哪些字段一致
            head += "；一致：" + "、".join(ok_fields)
    else:
        head = "✓ 一致：" + "、".join(ok_fields) if ok_fields else "? 无可比字段"
    if notes:
        head += "；（" + "、".join(notes) + "）"
    return (not diffs), head


def compare_record(sheet_row: dict, rec) -> tuple[bool, str]:
    """返回 (是否全部一致, 写入 M 列的文字)。

    sheet_row: {'manufacturer':..., 'factory':..., 'model':..., 'standard':..., 'cert_status':...}
    rec: CertRecord
    """
    from .models import BLOCKED, FOUND, NOT_FOUND, UNSUPPORTED

    if rec.status == NOT_FOUND:
        return False, f"⚠ 官网未查到该证书号（{rec.source}）{('：' + rec.message) if rec.message else ''}"
    if rec.status == BLOCKED:
        return False, f"✗ 网络被拦截，未能查询（{rec.source}）：{rec.message}"
    if rec.status == UNSUPPORTED:
        return False, f"– 未查询（{rec.source}）：{rec.message}"
    if rec.status != FOUND:
        return False, f"✗ 查询失败（{rec.source}）：{rec.message}"

    return summarize_fields(compare_fields(sheet_row, rec))
