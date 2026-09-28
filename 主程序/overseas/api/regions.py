"""地区 → 站点 → 任务能力 的元数据（前端地区切换与站点验证用）。

面向"多地区、每地区目标站点不同"的诉求。目前落地日本线；欧美/加拿大等
预留占位（enabled=False），补齐适配器后把 enabled 改 True 即可，前端无需改动。

每个地区声明三类能力的目标：
  spec   ：规格来源（日本=各品牌官网；其它地区可能是电商站规格页）
  price  ：价格来源
  review ：评价来源
每个能力给出 site（适配器 code）、可选品牌清单、以及一个用于"通道验证"的 probe_url。
"""
from __future__ import annotations

from typing import Any

from ..regions_config import REGIONS as _CONFIG_REGIONS
from ..regions_config import get_region as _get_region_cfg

# 日本 SPEC 六品牌官网入口（既有适配器）
_JP_SPEC_SITES = [
    {"site": "hisense_jp", "brand": "hisense_jp", "name": "Hisense", "probe_url": "https://www.hisense.co.jp/tv/"},
    {"site": "sony_jp", "brand": "sony_jp", "name": "SONY", "probe_url": "https://www.sony.jp/bravia/lineup/"},
    {"site": "regza_jp", "brand": "regza_jp", "name": "REGZA", "probe_url": "https://www.regza.com/tv/lineup"},
    {"site": "panasonic_jp", "brand": "panasonic_jp", "name": "Panasonic", "probe_url": "https://panasonic.jp/viera/products.html"},
    {"site": "tcl_jp", "brand": "tcl_jp", "name": "TCL", "probe_url": "https://www.tcl.com/jp/ja/tvs"},
    {"site": "sharp_jp", "brand": "sharp_jp", "name": "SHARP", "probe_url": "https://www.sharp.co.jp/aquos/lineup/"},
]

# 日本电视六品牌（价格/评价都走 kakaku.com，按品牌抓）
_JP_BRANDS = ["hisense_jp", "sony_jp", "regza_jp", "panasonic_jp", "tcl_jp", "sharp_jp"]


def _build_ca_region() -> dict[str, Any]:
    """从集中配置构造加拿大完整 API 元数据。"""
    cfg = _get_region_cfg("ca")
    if cfg is None:
        return {"code": "ca", "name": "加拿大", "enabled": False,
                "currency": "CAD", "locale": "en-CA",
                "brands": [], "spec_sites": [], "retail_sites": [],
                "note": "regions_config 未配置 ca"}

    brands = [{"code": b.code, "name": b.name, "search_name": b.query_name}
              for b in cfg.brands]
    spec_sites = [{"site": s.site, "brand": s.brand, "name": s.name,
                   "probe_url": s.probe_url} for s in cfg.spec_sites]
    all_retail = [{"site": r.site, "name": r.name, "probe_url": r.probe_url,
                   "enabled": r.enabled} for r in cfg.retail_sites]
    enabled_retail = [r for r in all_retail if r["enabled"]]
    return {
        "code": cfg.code,
        "name": cfg.name,
        "enabled": cfg.enabled,
        "currency": cfg.currency,
        "locale": cfg.locale,
        "note": cfg.note,
        "brands": brands,
        "spec_sites": spec_sites,
        "retail_sites": all_retail,
        "spec": {"task": "spec", "desc": "各品牌加拿大官网规格页",
                  "sites": spec_sites, "brands": [b["code"] for b in brands]},
        "price": {"task": "price", "desc": "多零售站价格（与网评同步抓取）",
                  "sites": enabled_retail, "brands": [b["code"] for b in brands]},
        "review": {"task": "review", "desc": "多零售站评价（与价格同步抓取）",
                   "sites": enabled_retail, "brands": [b["code"] for b in brands]},
        "capabilities": {"spec": True, "price": bool(enabled_retail),
                          "review": bool(enabled_retail)},
    }


REGIONS: list[dict[str, Any]] = [
    {
        "code": "jp",
        "name": "日本",
        "enabled": True,
        "currency": "JPY",
        "locale": "ja-JP",
        "brands": [{"code": code, "name": name} for code, name in zip(
            _JP_BRANDS, ["Hisense", "SONY", "REGZA", "Panasonic", "TCL", "SHARP"])
        ],
        "spec": {
            "task": "spec",
            "desc": "各品牌官网规格页",
            "sites": _JP_SPEC_SITES,
        },
        "price": {
            "task": "price",
            "desc": "価格.com 各店铺分列价格",
            "site": "kakaku_jp",
            "brands": _JP_BRANDS,
            "probe_url": "https://kakaku.com/kaden/lcd-tv/",
        },
        "review": {
            "task": "review",
            "desc": "価格.com レビュー / クチコミ",
            "site": "kakaku_jp",
            "brands": _JP_BRANDS,
            "probe_url": "https://kakaku.com/kaden/lcd-tv/",
        },
    },
    _build_ca_region(),
    {
        "code": "us",
        "name": "美国",
        "enabled": False,
        "note": "待补充目标站点与适配器",
    },
    {
        "code": "eu",
        "name": "欧洲",
        "enabled": False,
        "note": "待补充目标站点与适配器",
    },
]

REGION_BY_CODE = {r["code"]: r for r in REGIONS}

# 品牌 code → 展示名（与其它模块一致）
BRAND_DISPLAY = {
    "hisense_jp": "Hisense",
    "sony_jp": "SONY",
    "regza_jp": "REGZA",
    "panasonic_jp": "Panasonic",
    "tcl_jp": "TCL",
    "sharp_jp": "SHARP",
}
for _cfg in _CONFIG_REGIONS.values():
    for _brand in _cfg.brands:
        BRAND_DISPLAY.setdefault(_brand.code, _brand.name)


def all_probe_targets(region_code: str = "") -> list[dict[str, str]]:
    """列出可用于通道验证的 (地区, 能力, 站点, URL)。region_code 空=全部地区。"""
    out: list[dict[str, str]] = []
    for region in REGIONS:
        if region_code and region["code"] != region_code:
            continue
        if not region.get("enabled"):
            continue
        for cap in ("spec", "price", "review"):
            block = region.get(cap)
            if not block:
                continue
            if "sites" in block:
                for s in block["sites"]:
                    out.append({"region": region["code"], "cap": cap,
                                "site": s["site"], "name": s.get("name", s["site"]),
                                "url": s["probe_url"]})
            elif block.get("probe_url"):
                out.append({"region": region["code"], "cap": cap,
                            "site": block.get("site", ""),
                            "name": block.get("desc", cap), "url": block["probe_url"]})
    # 按 URL 去重（同一 kakaku 入口 price/review 共用），保留首次出现
    seen: set[str] = set()
    uniq: list[dict[str, str]] = []
    for t in out:
        if t["url"] in seen:
            continue
        seen.add(t["url"])
        uniq.append(t)
    return uniq
