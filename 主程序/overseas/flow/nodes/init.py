"""init_node — 初始化状态、备份DB、加载站点列表。

边界条件：
- 磁盘空间 < 1GB → 暂停 + 告警
- 站点列表为空 → 跳过该线
"""
from __future__ import annotations
import os, sys, shutil, json, sqlite3, datetime
from typing import Any
from ..state import CrawlState

# 线路 → 国家映射（中英文兼容）
LINE_COUNTRIES = {
    "japan": {"JAPAN", "日本"},
    "na": {"USA", "UNITED STATES", "CANADA", "MEXICO", "墨西哥"},
    "sa": {"BRAZIL", "COLOMBIA", "ARGENTINA", "CHILE", "PERU", "ECUADOR",
           "URUGUAY", "PARAGUAY", "BOLIVIA", "VENEZUELA", "GUATEMALA",
           "PANAMA", "COSTA RICA", "EL SALVADOR", "HONDURAS",
           "NICARAGUA", "DOMINICAN REPUBLIC"},
    "eu": {"UNITED KINGDOM", "GERMANY", "FRANCE", "ITALY", "SPAIN",
           "PORTUGAL", "NETHERLANDS", "BELGIUM", "AUSTRIA", "SWITZERLAND",
           "IRELAND", "SWEDEN", "NORWAY", "DENMARK", "FINLAND", "POLAND",
           "CZECH", "HUNGARY", "GREECE", "ROMANIA", "BULGARIA", "CROATIA",
           "SLOVAKIA", "SLOVENIA", "ESTONIA", "LATVIA", "LITHUANIA",
           "LUXEMBOURG", "MALTA", "CYPRUS", "SERBIA", "BOSNIA AND HERZEGOVINA"},
    "asia": {"RUSSIA", "TURKEY", "TURKIYE", "KAZAKHSTAN", "INDIA",
             "SOUTH KOREA", "TAIWAN", "SINGAPORE", "MALAYSIA", "THAILAND",
             "VIETNAM", "INDONESIA", "PHILIPPINES", "UNITED ARAB EMIRATES",
             "SAUDI ARABIA", "ISRAEL", "QATAR", "KUWAIT", "BAHRAIN", "OMAN",
             "UKRAINE"},
}


def _get_sites_for_line(line: str) -> tuple[list[str], list[str], list[str]]:
    """返回 (spec_sites, retail_sites, retail_with_data)"""
    from overseas.sites import SiteRegistry
    countries = LINE_COUNTRIES.get(line, set())
    countries_upper = {c.upper() for c in countries}
    spec_sites, retail_sites, retail_with_data = [], [], []

    con = sqlite3.connect("data/overseas.db")
    for code in sorted(SiteRegistry.codes()):
        ad = SiteRegistry.get(code)
        country = (ad.country or "").upper()
        if line == "japan" and not (code.endswith("_jp") or country in countries_upper):
            continue
        if line != "japan" and country not in countries_upper:
            continue

        is_spec = getattr(ad, "supports_spec", False)
        is_retail = getattr(ad, "supports_retail", False)

        if is_spec and not is_retail:
            spec_sites.append(code)
        else:
            retail_sites.append(code)
            # 检查是否有产品
            r = con.execute("SELECT id FROM site WHERE code=?", (code,)).fetchone()
            if r:
                p = con.execute("SELECT COUNT(*) FROM product WHERE site_id=?", (r[0],)).fetchone()[0]
                if p > 0:
                    retail_with_data.append(code)

    con.close()
    return spec_sites, retail_sites, retail_with_data


def _check_disk_space() -> float:
    """返回C盘可用空间(GB)"""
    try:
        import shutil as _s
        total, used, free = _s.disk_usage("C:\\")
        return free / (1024 ** 3)
    except Exception:
        return 999.0


def _get_db_stats(db_path: str) -> tuple[int, int, int]:
    """返回 (spec_row, price_count, review_count)"""
    con = sqlite3.connect(db_path)
    sr = con.execute("SELECT COUNT(*) FROM spec_row").fetchone()[0]
    pr = con.execute("SELECT COUNT(*) FROM price_snapshot").fetchone()[0]
    rv = con.execute("SELECT COUNT(*) FROM review").fetchone()[0]
    con.close()
    return sr, pr, rv


def init_node(state: CrawlState) -> CrawlState:
    """初始化节点：备份DB、加载站点列表、记录基线数据。"""
    db_path = state.get("db_path", "data/overseas.db")
    line = state.get("current_line", "")

    # 1. 检查磁盘空间
    free_gb = _check_disk_space()
    if free_gb < 1.0:
        state["errors"] = state.get("errors", []) + [
            f"磁盘空间不足: {free_gb:.1f}GB < 1GB，暂停执行"
        ]
        state["stage"] = "disk_full"
        return state

    # 2. 备份DB（仅首次或每线开始时）
    if not state.get("backup_done"):
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        bak = f"{db_path}.bak_{ts}"
        if os.path.exists(db_path):
            shutil.copy2(db_path, bak)
        state["backup_done"] = True

    # 3. 加载站点列表
    spec_sites, retail_sites, retail_with_data = _get_sites_for_line(line)
    state["spec_sites"] = spec_sites
    state["retail_sites"] = retail_sites
    state["retail_sites_with_data"] = retail_with_data

    # 4. 记录基线数据
    sr, pr, rv = _get_db_stats(db_path)
    state["spec_row_before"] = sr
    state["price_before"] = pr
    state["review_before"] = rv

    # 5. 设置默认值
    state.setdefault("max_retries", 2)
    state.setdefault("per_site_timeout", 90)
    state.setdefault("failed_sites", [])
    state.setdefault("blocked_sites", [])
    state.setdefault("timeout_sites", [])
    state.setdefault("retry_count", {})
    state.setdefault("spec_results", {})
    state.setdefault("retail_results", {})
    state.setdefault("review_results", {})
    state.setdefault("errors", [])

    # 6. 当前周
    now = datetime.date.today()
    iso_year, iso_week, _ = now.isocalendar()
    state["week"] = f"{iso_year}-W{iso_week:02d}"

    state["stage"] = "spec"
    state["site_index"] = 0

    print(f"[INIT] 线={line} SPEC站={len(spec_sites)} RETAIL站={len(retail_sites)} "
          f"有数据RETAIL={len(retail_with_data)} 磁盘={free_gb:.1f}GB "
          f"基线: spec_row={sr} price={pr} review={rv}")

    return state
