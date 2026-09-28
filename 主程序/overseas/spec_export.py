"""SPEC 导出：一个品牌一个 Excel，格式对齐参考图。

列布局：区分 | 项目 | 项目(中文) | 机型1 | 机型2 | ...（该品牌所有系列的机型横排）
行：所有系列的 SPEC 项，按 (区分, 项目) 合并去重，保持官网出现顺序。
中文列紧挨在「项目」右侧。

导出优先取最近一次入口审计中的成功系列和当前周快照；没有入口审计时才回退到历史系列。
"""
from __future__ import annotations

import json
import os
import re

from .db import Database
from .dict import translate
from .spec_items import canonical_item_ja
from .sites import SiteRegistry
from .spec_identity import display_series_name

HEADER = ["区分", "项目", "项目（中文）"]

# 「上市时间」行的区分/项目名（放在表格顶部，用于按上市时间筛选机型）
RELEASE_CATEGORY = "上市时间"
RELEASE_ITEM_JA = "発売日"


def _norm_model(s: str) -> str:
    """型号归一化：去空格/短横/下划线并大写，便于跨来源匹配发售日。"""
    return "".join(ch for ch in (s or "").upper() if ch.isalnum())


def _audit_model_name(value) -> str:
    """从入口审计的字符串或型号元数据对象中取出型号名。"""
    if isinstance(value, dict):
        for key in ("model", "model_raw", "model_normalized", "name"):
            text = value.get(key)
            if text:
                return str(text).strip()
        return ""
    return str(value or "").strip()


def _norm_item_ja(s: str) -> str:
    """项目（日文）归一化，用作跨系列合并键。

    只做「不改变含义」的归一化，避免误合并：
      - NFKC：全角/半角统一（２→2、（）→()、全角空格→半角）
      - 去掉全部空白
      - 统一斜杠（／→/），并压缩连续斜杠（`/ /` → `/`）
    不含区分（category）：同一规格项在不同系列被归到不同区分（或区分为空）时，
    过去按 (区分, 日文) 做键会被拆成多行；改为只按归一化日文合并成一行。
    含义相反的项（如「含底座 / 不含底座」）日文本身不同，归一化后键仍不同，不会误合。
    """
    import unicodedata
    raw = canonical_item_ja(s or "")
    # 先去圏号商标符（NFKC 会把 Ⓡ 变成字母 R，污染键，故在归一化前删除）
    for mark in ("®", "Ⓡ", "™", "©", "㋿", "〇"):
        raw = raw.replace(mark, "")
    t = unicodedata.normalize("NFKC", raw)
    t = re.sub(r"\s+", "", t)
    t = t.replace("／", "/")
    t = re.sub(r"/{2,}", "/", t)          # 连续斜杠压成一个（'裏番組録画//2番組' 等）
    # 去脚注/序号噪声（不改变规格含义）：
    #   - 圏号 ® Ⓡ ㊙ 等统一去掉（HDMI® vs HDMIⓇ）
    #   - 脚注标记 ※N / ＊N / *N（N 为数字，官网表格脚注编号）
    #   - 行尾游离数字（官网导出带的行号，如 'Google TV6'、'MHL端子21'）
    t = re.sub(r"[※*＊][0-9]+", "", t)     # ※3 / *18 / ＊11 脚注编号
    t = re.sub(r"[※*＊]", "", t)           # 落单的脚注符（无编号）
    t = re.sub(r"[0-9]+$", "", t)          # 行尾游离数字（行号）
    # 把「主干」和「括号/※ 备注」拆开：备注集合排序后重组，
    # 使「括号顺序颠倒」「/ 与 () 混用的备注」这类同项异写归一到同一键，且不丢字。
    # 例：年間消費電力(A)/※B  与  年間消費電力(※B)(A)  →  主干「年間消費電力」+ 备注{A,※B}
    notes = re.findall(r"[（(]([^）)]*)[）)]", t)          # 括号内备注
    trunk = re.sub(r"[（(][^）)]*[）)]", "", t)             # 去掉括号后的主干（含斜杠段）
    # 主干里以 '/' 或 '※' 引出的备注也拆出来（如 待機電力/※リモコン…、省エネ…/※目標…）
    seg = re.split(r"[/※]", trunk)
    trunk_main = seg[0]
    for extra in seg[1:]:
        if extra:
            notes.append(extra)
    # 折叠整串对半重复（TCL 脏数据：'視聴年齢制限番組対応視聴年齢制限番組対応'）。
    trunk_main = _fold_repeat(trunk_main)
    # 枚举取值型项目：主干为项目名、后面挂一长串「取值清单」（各系列不同），
    # 清单是内容不是项目区分维度，归键时丢弃，使同一项目跨系列合并成一行。
    # 例：'VOD機能 Youtube/NetFlix/...' 各系列服务列表不同，都应归到 'VOD機能'。
    for _enum_key in _ENUM_VALUE_ITEMS:
        if trunk_main.startswith(_enum_key):
            return _enum_key
    # 主干同义词归一（官网异写，语义完全相同，合并不丢信息）：
    #   スタンドなし ↔ スタンド除く（都表示「不含底座」）
    #   梱梱包カートン ↔ 梱包カートン（重复字脏数据）
    for a, b in _TRUNK_SYNONYM:
        trunk_main = trunk_main.replace(a, b)
    trunk_main = _fold_repeat(trunk_main)     # 同义替换后可能又出现对半重复，再折一次
    # note 归一：单位大小写统一（Kg→kg）、年度基準→年基準（同义），再去重。
    norm_notes = []
    for n in notes:
        nn = n.lstrip("※").strip()
        if not nn:
            continue                       # 空 note（如 (※2) 去脚注后剩空括号），丢弃
        nn = re.sub(r"kg", "kg", nn, flags=re.IGNORECASE)   # Kg/KG/ｋｇ → kg
        nn = nn.replace("年度基準", "年基準")
        norm_notes.append(nn)
    notes = sorted(set(norm_notes))
    key = trunk_main + "|" + "|".join(notes) if notes else trunk_main
    return _ITEM_KEY_ALIAS.get(key, key)


# 主干同义异写：官网对同一项目的不同写法/脏数据，语义相同，归一到统一主干。
_TRUNK_SYNONYM: tuple[tuple[str, str], ...] = (
    ("梱梱包カートン", "梱包カートン"),      # 重复字脏数据
    ("スタンドなし", "スタンド除く"),        # なし = 除く，都表示「不含底座」
)


# 「取值枚举型」项目：项目名后跟随一长串服务/格式清单，清单随系列变化，
# 归键时只保留项目名主干。仅列入确认属此类的项目，避免误伤。
_ENUM_VALUE_ITEMS: tuple[str, ...] = ("VOD機能",)


def _fold_repeat(s: str) -> str:
    """把「整串正好对半、且前后两半相等」折叠成一半（去官网重复脏数据）。"""
    n = len(s)
    if n >= 4 and n % 2 == 0 and s[:n // 2] == s[n // 2:]:
        return s[:n // 2]
    return s


# 别名归一：把人工确认过的「同项异写」（差个别助词/分词，通用规则不宜覆盖的）
# 精确映射到统一键。只在此表内的写法受影响，其它项零风险。key 为 _norm_item_ja
# 在应用本表前算出的中间键。新增同类合并时在此追加一行即可。
_ITEM_KEY_ALIAS: dict[str, str] = {
    # ---- 海信 ----
    # 待機電力：'リモコンでの電源OFF時'（多助词 で）统一到 'リモコンの電源OFF時'
    "待機電力|リモコンでの電源OFF時": "待機電力|リモコンの電源OFF時",
    # Bluetooth 耳机：连写 'Bluetoothイヤホン' 统一到分写 'Bluetooth|イヤホン'
    "Bluetoothイヤホン|サウンドバー|ヘッドホン": "Bluetooth|イヤホン|サウンドバー|ヘッドホン",

    # ---- REGZA ----（括号是同项补充/汉字假名差异，确认同义后合并）
    "Vポイントがたまる": "Vポイントが貯まる",
    "スピーカー|個数": "スピーカー",
    "色温度センサー|RGBセンサー": "色温度センサー",
    # マルチウインドウ / マルチウィンドウ（ウイ↔ウィ 假名异写，同为「多窗口」）
    "マルチウインドウ": "マルチウィンドウ",

    # ---- Panasonic ----
    # VIERA Link 英文名 ↔ 片假名「ビエラリンク」，同一功能
    "VIERALink対応": "ビエラリンク対応",

    # ---- TCL ----（括号是单位/技术名补充，指同一项）
    "消費電力|W": "消費電力",
    "待機時消費電力|W": "待機時消費電力",
    "CEC対応|HDMI2.0a": "CEC対応",
    "HDCP|ポート毎V2.2/1.4/Auto": "HDCP",
    "量子ドット|QLED": "量子ドット",
    "動き補正搭載|MEMC": "動き補正搭載",
    "リモコン用乾電池|単4": "リモコン用乾電池",
    "電子番組表|ラテ欄": "電子番組表|EPG",
    "年間消費電力量|KWh/年": "年間消費電力量",
    "VOD機能|YouTube/Amazonプライムビデオ/NETFLIX/hulu/U-NEXT/AbemaTV等ネット動画サービス": "VOD機能",
    "VOD機能|YouTube/NETFLIX/hulu/U-NEXT/AbemaTV等ネット動画サービス": "VOD機能",
    # 局部/微区调光：片假名「ディミング」↔ 英文「dimming」，同一技术
    "ローカルdimming": "ローカルディミング",
    "マイクロdimming": "マイクロディミング",
    # Google 助理：「アシスタント」↔「アシスト」官网两种写法，同为内置 Google 助理
    "Googleアシスト搭載": "Googleアシスタント搭載",
    # ONKYO 扬声器：品牌大小写异写 ONKYO ↔ Onkyo
    "Onkyoスピーカー": "ONKYOスピーカー",
    # 网页浏览器：片假名「ウェブブラウザ」↔ 英文「Web Browser」
    "WebBrowser": "ウェブブラウザ―",
    # 多媒体播放器：片假名 ↔ 英文「MultiMedia Player」
    "MultiMediaPlayer": "マルチメディアプレイヤー",
    # VOD 变体：官网这行漏了「VOD機能」前缀，主干变成服务列表首项，单独归并
    "NetFlix|AWA)|AbemaTV|FOD|GYAO|Hulu|RakutenTV|SOD|U-NEXT|Youtube|dTV": "VOD機能",
    # VESA 尺寸：官网三种异写（縦x横 是额外维度描述、金具金具 是重复脏数据），同一项
    "VESA寸法|mm|市販金具|縦x横": "VESA寸法|mm|市販金具",
    "VESA寸法|mm|市販金具金具": "VESA寸法|mm|市販金具",
    # 音频/字幕切换：note 里 '字幕の切換え音声' 是官网重复脏数据，归并到单条
    "音声|字幕の切換え|字幕の切換え音声": "音声|字幕の切換え",
    # 注意：以下故意【不】合并，因中文虽同但含义不同（尺寸 vs 重量、基准年不同）：
    #   スタンドあり|mm ≠ |Kg、スタンド除く|mm ≠ |Kg、梱包カートン|mm ≠ |Kg、
    #   省エネ達成率 2026年基準 ≠ 2012年基準。这些中文撞车属翻译层问题，已在词典层
    #   补齐大写 Kg / 2012年(度)基準 精确条目，使中文能区分 mm/kg、2026/2012。
}


def load_release_map(path: str | os.PathLike | None) -> dict[str, str]:
    """读取「型号→上市时间」映射（crawl_kakaku_reviews.py --release-map 产出）。

    返回 {归一化型号: 上市时间展示值}。文件不存在或无效时返回空 dict。
    """
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, str] = {}
    if isinstance(raw, dict):
        for model, info in raw.items():
            rel = ""
            if isinstance(info, dict):
                rel = str(info.get("release") or info.get("release_raw") or "")
            elif isinstance(info, str):
                rel = info
            if rel:
                out[_norm_model(model)] = rel
    return out


def _spec_entry_scope(db: Database, brand: str) -> dict | None:
    """读取最近一次入口审计，返回当前入口对应的成功系列/型号范围。

    旧审计的 ``details_json`` 可能为空：单次完整运行直接使用该 run 的成功系列；
    若最近一次审计属于 ``--series`` 补跑，则把同一周、从最近一次完整运行开始的
    补跑结果合并。这样既不删除历史 ``spec_series``，也不会把历史系列带入当前导出。
    """
    audit = db.latest_spec_entry_audit(brand)
    if audit is None:
        return None

    def _params(row) -> dict:
        try:
            value = json.loads(row["params"] or "{}")
        except (TypeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    run_rows = db.conn.execute(
        "SELECT id, params, status, started_at FROM crawl_run "
        "WHERE scenario=? AND site_code=? ORDER BY id",
        ("spec_crawl", brand),
    ).fetchall()
    run_info = {int(row["id"]): (row, _params(row)) for row in run_rows}
    audit_run_id = int(audit["run_id"] or 0)
    audit_params = run_info.get(audit_run_id, (None, {}))[1]
    week = str(audit_params.get("week") or "")
    run_ids = {audit_run_id} if audit_run_id else set()

    # Hisense 等站点会先做完整入口运行，再用 --series 补跑被拦截/超时系列。
    # 审计行属于补跑时，合并从最近完整运行开始、截至该审计时间的同周运行。
    if audit_params.get("only"):
        full_runs = []
        for row, params in run_info.values():
            if (params.get("week") == week and not params.get("only")
                    and row["status"] in {"completed", "failed"}):
                full_runs.append(row)
        if full_runs:
            anchor = max(full_runs, key=lambda row: (row["started_at"] or "", row["id"]))
            anchor_started = anchor["started_at"] or ""
            captured_at = str(audit["captured_at"] or "")
            for row, params in run_info.values():
                started = row["started_at"] or ""
                if params.get("week") != week or started < anchor_started:
                    continue
                if captured_at and started > captured_at:
                    continue
                run_ids.add(int(row["id"]))
            run_ids.add(audit_run_id)

    status_rows = []
    if run_ids:
        marks = ",".join("?" for _ in run_ids)
        status_rows = db.conn.execute(
            f"SELECT series, captured_week, status, series_id "
            f"FROM spec_series_status WHERE run_id IN ({marks}) ORDER BY finished_at, id",
            tuple(sorted(run_ids)),
        ).fetchall()
    if not week:
        weeks = [str(row["captured_week"] or "") for row in status_rows
                 if row["captured_week"]]
        week = max(weeks) if weeks else ""

    successful = [row for row in status_rows
                  if row["status"] == "success" and row["series_id"] is not None]
    series_ids = {int(row["series_id"]) for row in successful}
    series_names = {str(row["series"] or "") for row in successful if row["series"]}

    try:
        details = json.loads(audit["details_json"] or "{}")
    except (TypeError, json.JSONDecodeError):
        details = {}
    if not isinstance(details, dict):
        details = {}
    model_order: list[str] = []
    model_norms: set[str] = set()
    for entry in details.get("series") or []:
        if not isinstance(entry, dict):
            continue
        for model in entry.get("models") or []:
            text = _audit_model_name(model)
            norm = _norm_model(text)
            if norm and norm not in model_norms:
                model_norms.add(norm)
                model_order.append(text)

    return {
        "series_ids": series_ids,
        "series_names": series_names,
        "week": week,
        "model_norms": model_norms,
        "model_order": model_order,
    }


def _collect(db: Database, brand: str, release_map: dict[str, str] | None = None):
    """汇总当前入口范围内各系列的最新周数据。

    有入口审计时只导出本次入口中成功写入的系列；没有审计时才回退到历史全量。
    这样保留数据库历史快照，同时避免历史系列/型号污染当前交付表。
    """
    entry_scope = _spec_entry_scope(db, brand)
    series_rows = db.spec_series_list(brand)
    if entry_scope is not None:
        current_ids = entry_scope["series_ids"]
        current_names = entry_scope["series_names"]
        if current_ids:
            series_rows = [s for s in series_rows if int(s["id"]) in current_ids]
        elif current_names:
            series_rows = [s for s in series_rows if (s["series"] or "") in current_names]
        else:
            # 当前入口有审计但本周没有成功系列时，不回退到旧 SPEC。
            series_rows = []
    if not series_rows:
        return [], []

    all_models: list[str] = []
    # 有序去重：key=(category, item_ja)
    order_keys: list[tuple[str, str]] = []
    row_map: dict[tuple[str, str], dict] = {}

    # 系列名集合：用于过滤「シリーズ」行（项目名=系列号，是每表首行噪音）
    series_names = {(s["series"] or "").upper().replace(" ", "") for s in series_rows}

    # 机型定义行的项目名（值是机型列表，非规格），一律不作为 SPEC 项导出
    _MODEL_DEF_ITEMS = {"型", "型番", "品番", "型名", "シリーズ", "系列"}

    def _is_series_row(item: str) -> bool:
        raw = (item or "").strip()
        if raw in _MODEL_DEF_ITEMS:
            return True
        key = raw.upper().replace(" ", "")
        if not key:
            return False
        # 直接相等，或与某系列号互为子串（如 U9R ⊂ 116U9R、U88R ⊂ 50U88R）
        for sn in series_names:
            if key == sn or (len(key) >= 3 and (key in sn or sn in key)):
                return True
        return False

    for s in series_rows:
        series_id = s["id"]
        if entry_scope is not None:
            week = str(entry_scope["week"] or "")
            if not week:
                continue
        else:
            # 无入口审计的旧数据仍按系列取最新周，保持向后兼容。
            wk = db.conn.execute(
                "SELECT MAX(captured_week) w FROM spec_row WHERE series_id=?",
                (series_id,)).fetchone()
            week = wk["w"] if wk and wk["w"] else ""
            if not week:
                continue
        smodels = json.loads(s["models"] or "[]")
        if entry_scope is not None and entry_scope["model_norms"]:
            smodels = [m for m in smodels
                       if _norm_model(m) in entry_scope["model_norms"]]
        for m in smodels:
            if m not in all_models:
                all_models.append(m)
        for r in db.spec_rows_for_series(series_id, week):
            if _is_series_row(r["item_ja"]):
                continue                      # 跳过「シリーズ」行（系列名，非 SPEC 项）
            # 合并键：归一化日文（忽略区分）。同项在不同系列的区分不一致/为空时也能合并。
            key = _norm_item_ja(r["item_ja"])
            if key not in row_map:
                row_map[key] = {"cat": r["category"] or "", "item": r["item_ja"] or "",
                                "zh": r["item_zh"] or "", "vals": {}}
                order_keys.append(key)
            slot = row_map[key]
            # 区分取第一个非空；日文/中文保留首次出现的写法
            if not slot["cat"] and r["category"]:
                slot["cat"] = r["category"]
            if not slot["item"] and r["item_ja"]:
                slot["item"] = r["item_ja"]
            vals = json.loads(r["values_json"] or "{}")
            # 通配 '*'（全机型同值）展开到该系列机型
            if "*" in vals:
                for m in smodels:
                    slot["vals"].setdefault(m, vals["*"])
            for m, v in vals.items():
                if m != "*":
                    slot["vals"][m] = v
            if not slot["zh"] and r["item_zh"]:
                slot["zh"] = r["item_zh"]

    if entry_scope is not None and entry_scope["model_order"]:
        model_positions = {
            _norm_model(model): index
            for index, model in enumerate(entry_scope["model_order"])
        }
        all_models.sort(key=lambda model: model_positions.get(
            _norm_model(model), len(model_positions)))

    rows = []
    # 顶部插入「上市时间」行：每个机型一个发售日（按归一化型号匹配 release_map）。
    if release_map:
        rel_vals = {}
        for m in all_models:
            rel = release_map.get(_norm_model(m), "")
            if rel:
                rel_vals[m] = rel
        if rel_vals:
            rows.append((RELEASE_CATEGORY, RELEASE_ITEM_JA,
                         translate(RELEASE_ITEM_JA) or "上市时间", rel_vals))
    for key in order_keys:
        d = row_map[key]
        zh = d["zh"]
        if not zh:                       # 库里未翻译时用当前词典兜底（词典更新后无需重采）
            zh = translate(d["item"])
        rows.append((d["cat"], d["item"], zh, d["vals"]))
    return all_models, rows


def export_csv(db: Database, brand: str, path: str,
               release_map: dict[str, str] | None = None) -> int:
    import csv
    models, rows = _collect(db, brand, release_map)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEADER + models)
        for cat, item, zh, vals in rows:
            w.writerow([cat, item, zh] + [vals.get(m, "") for m in models])
    return len(rows)


def export_xlsx(db: Database, brand: str, path: str,
                release_map: dict[str, str] | None = None) -> int:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError as e:
        raise RuntimeError("导出 xlsx 需要 openpyxl：pip install openpyxl") from e

    models, rows = _collect(db, brand, release_map)
    brand_name = brand
    sl = db.spec_series_list(brand)
    if sl:
        brand_name = sl[0]["brand_name"] or brand

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = brand_name[:28] or "SPEC"
    headers = HEADER + models
    ws.append(headers)

    fill = PatternFill("solid", fgColor="E38B29")     # 橙色表头（对齐参考图）
    zh_fill = PatternFill("solid", fgColor="FBE2C6")  # 中文列淡橙，突出
    font = Font(color="FFFFFF", bold=True)
    center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for c in ws[1]:
        c.fill = fill
        c.font = font
        c.alignment = center

    last_cat = None
    for cat, item, zh, vals in rows:
        # 同区分连续行只在首行显示区分名，视觉上更接近参考图的合并效果
        show_cat = cat if cat != last_cat else ""
        last_cat = cat
        ws.append([show_cat, item, zh] + [vals.get(m, "") for m in models])
        # 中文列（第3列）淡橙底
        ws.cell(ws.max_row, 3).fill = zh_fill

    # 列宽
    ws.column_dimensions["A"].width = 12
    ws.column_dimensions["B"].width = 26
    ws.column_dimensions["C"].width = 22
    for i in range(len(models)):
        ws.column_dimensions[get_column_letter(4 + i)].width = 18
    ws.freeze_panes = "D2"       # 冻结前3列和表头
    wb.save(path)
    return len(rows)
