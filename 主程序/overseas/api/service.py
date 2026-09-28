"""查询服务层：把数据库行整理成面向前端的结构。"""
from __future__ import annotations

import json
import os
from typing import Any

from ..db import Database
from ..regions_config import get_region
from ..spec_export import _collect as spec_collect
from ..spec_export import load_release_map
from ..spec_identity import display_series_name

# 日本电视 SPEC 六品牌（顺序即前端展示顺序）
SPEC_BRANDS = ("hisense_jp", "sony_jp", "regza_jp",
               "panasonic_jp", "tcl_jp", "sharp_jp")

BRAND_DISPLAY = {
    "hisense_jp": "Hisense", "sony_jp": "SONY", "regza_jp": "REGZA",
    "panasonic_jp": "Panasonic", "tcl_jp": "TCL", "sharp_jp": "SHARP",
}


def _norm_model(s: str) -> str:
    return "".join(ch for ch in (s or "").upper() if ch.isalnum())


def _page(items: list, limit: int, offset: int) -> dict[str, Any]:
    total = len(items)
    return {
        "items": items[offset: offset + limit] if limit > 0 else items[offset:],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


class Service:
    """查询服务。每个请求新建实例（SQLite 连接不跨线程复用）。"""

    def __init__(self, db_path: str | None = None):
        self.db = Database(db_path)
        # API 可能直接绑定一个旧库；启动查询前幂等建表并补齐 schema v7，
        # 确保 CA 的 retail_task_status 和入口审计表已存在。
        self.db.init_schema()

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "Service":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------------- 地区/品牌/产品边界 ----------------

    @staticmethod
    def _region_code(region: str) -> str:
        return (region or "jp").strip().lower()

    def _validate_region(self, region: str) -> str:
        code = self._region_code(region)
        if code != "jp" and get_region(code) is None:
            raise ValueError(f"不支持的地区: {code}")
        return code

    def _brand_codes(self, region: str) -> list[str]:
        region = self._validate_region(region)
        if region == "jp":
            return list(SPEC_BRANDS)
        cfg = get_region(region)
        return cfg.brand_codes() if cfg else []

    def _brand_name(self, code: str, region: str = "jp") -> str:
        if code in BRAND_DISPLAY:
            return BRAND_DISPLAY[code]
        cfg = get_region(self._region_code(region))
        if cfg:
            for brand in cfg.brands:
                if brand.code == code:
                    return brand.name
        return code

    def _retail_sites(self, region: str, only_enabled: bool = True) -> set[str]:
        region = self._validate_region(region)
        cfg = get_region(region)
        return set(cfg.retail_site_codes(only_enabled=only_enabled)) if cfg else set()

    def _brand_allowed(self, region: str, brand: str) -> bool:
        code = (brand or "").strip()
        return not code or code in set(self._brand_codes(region))

    def _validate_brand(self, region: str, brand: str = "",
                        required: bool = False) -> str:
        code = (brand or "").strip()
        allowed = self._brand_codes(region)
        if required and not code:
            raise ValueError("缺少参数 brand")
        if code and code not in set(allowed):
            raise ValueError(f"品牌 {code} 不属于地区 {self._validate_region(region)}")
        return code

    def _data_file(self, filename: str) -> str:
        """按查询库所在目录读取同一条线的派生 JSON 文件。"""
        base = os.path.dirname(os.path.abspath(self.db.path))
        return os.path.join(base, filename)

    def _product_site(self, product_id: int):
        return self.db.conn.execute(
            "SELECT p.*, s.code AS site_code, s.name AS site_name "
            "FROM product p JOIN site s ON s.id=p.site_id WHERE p.id=?",
            (product_id,),
        ).fetchone()

    def _product_allowed(self, product_id: int, region: str,
                         brand: str = ""):
        """检查商品是否属于当前地区，并在给出品牌时检查任务状态归属。"""
        region = self._validate_region(region)
        row = self._product_site(product_id)
        if row is None:
            return None
        allowed = {"kakaku_jp"} if region == "jp" else self._retail_sites(region)
        if row["site_code"] not in allowed:
            return None
        code = (brand or "").strip()
        if not code:
            return row
        self._validate_brand(region, code, required=True)
        if region == "jp":
            associated = self.db.conn.execute(
                "SELECT 1 FROM kakaku_task_status "
                "WHERE product_id=? AND brand=? AND task IN ('price','review','release') "
                "LIMIT 1", (product_id, code),
            ).fetchone()
        else:
            site_marks = ",".join("?" for _ in allowed)
            associated = self.db.conn.execute(
                "SELECT 1 FROM retail_task_status "
                f"WHERE product_id=? AND region=? AND brand=? "
                f"AND site_code IN ({site_marks}) LIMIT 1",
                (product_id, region, code, *sorted(allowed)),
            ).fetchone()
        return row if associated else None

    # ---------------- 入口审计辅助 ----------------

    def _entry_audit(self, brand: str) -> dict[str, Any] | None:
        """把最近一次入口审计转换成可直接返回给前端的 JSON 对象。"""
        row = self.db.latest_spec_entry_audit(brand)
        if row is None:
            return None
        try:
            details = json.loads(row["details_json"] or "{}")
            if not isinstance(details, dict):
                details = {}
        except (TypeError, json.JSONDecodeError):
            details = {}

        normalized_series: list[dict[str, Any]] = []
        raw_series = details.get("series") or []
        if isinstance(raw_series, dict):
            raw_series = [raw_series]
        if not isinstance(raw_series, list):
            raw_series = []
        for group in raw_series:
            if isinstance(group, dict):
                series = str(group.get("series") or group.get("name") or "").strip()
                family_id = str(group.get("family_id") or "").strip()
                series_url = str(group.get("url") or "").strip()
                raw_models = group.get("models") or []
            else:
                series = str(group or "").strip()
                family_id = ""
                series_url = ""
                raw_models = []
            if isinstance(raw_models, dict):
                raw_models = [raw_models]
            models: list[dict[str, str]] = []
            if isinstance(raw_models, list):
                for entry in raw_models:
                    if isinstance(entry, dict):
                        model = str(entry.get("model") or entry.get("name") or "").strip()
                        url = str(entry.get("url") or entry.get("href") or series_url).strip()
                    else:
                        model = str(entry or "").strip()
                        url = series_url
                    if model:
                        models.append({"model": model, "url": url})
            if series or models:
                normalized_series.append({
                    "series": series,
                    "family_id": family_id,
                    "url": series_url,
                    "models": models,
                })
        public_details = dict(details)
        public_details["series"] = normalized_series
        return {
            "run_id": row["run_id"],
            "brand": row["brand"],
            "brand_name": row["brand_name"] or self._brand_name(brand),
            "requested_url": row["requested_url"] or "",
            "expected_series_count": row["expected_series_count"],
            "discovered_series_count": row["discovered_series_count"],
            "expected_model_count": row["expected_model_count"],
            "discovered_model_count": row["discovered_model_count"],
            "termination_reason": row["termination_reason"] or "",
            "captured_at": row["captured_at"] or "",
            "details": public_details,
        }

    def _entry_models(self, brand: str) -> list[dict[str, str]]:
        audit = self._entry_audit(brand)
        if not audit:
            return []
        result: list[dict[str, str]] = []
        for group in (audit.get("details") or {}).get("series") or []:
            series = str(group.get("series") or "")
            group_url = str(group.get("url") or "")
            for item in group.get("models") or []:
                model = str(item.get("model") or "").strip()
                if not model:
                    continue
                result.append({
                    "model": model,
                    "series": series,
                    "url": str(item.get("url") or group_url),
                })
        return result

    def _series_status_map(self, brand: str) -> dict[str, str]:
        result: dict[str, str] = {}
        for row in self.db.spec_series_status_list(brand=brand):
            series = str(row["series"] or "")
            if series and series not in result:
                result[series] = str(row["status"] or "unknown")
        return result

    def _retail_ok_model_count(self, region: str, brand: str, task: str) -> int:
        allowed = sorted(self._retail_sites(region))
        if not allowed:
            return 0
        marks = ",".join("?" for _ in allowed)
        row = self.db.conn.execute(
            "SELECT COUNT(DISTINCT r.model) n FROM retail_task_status r "
            f"WHERE r.region=? AND r.task=? AND r.brand=? AND r.status='ok' "
            f"AND r.site_code IN ({marks}) "
            "AND NOT EXISTS (SELECT 1 FROM retail_task_status newer "
            "WHERE newer.region=r.region AND newer.task=r.task "
            "AND newer.site_code=r.site_code AND newer.brand=r.brand "
            "AND newer.model=r.model AND newer.id>r.id)",
            (region, task, brand, *allowed),
        ).fetchone()
        return int(row["n"]) if row else 0

    # ---------------- 概览 ----------------

    def health(self) -> dict[str, Any]:
        return {
            "ok": True,
            "db": self.db.path,
            "schema_version": self.db.schema_version(),
            "stats": self.db.stats(),
        }

    def brands(self, region: str = "jp") -> list[dict[str, Any]]:
        """返回指定地区品牌概览；入口审计与已入库 SPEC 分开统计。"""
        region = self._validate_region(region)
        out: list[dict[str, Any]] = []
        for code in self._brand_codes(region):
            series_rows = self.db.spec_series_list(code)
            spec_series_names = {display_series_name(row["series"] or "")
                                 for row in series_rows
                                 if str(row["series"] or "")}
            spec_models: dict[str, str] = {}
            for row in series_rows:
                try:
                    raw_models = json.loads(row["models"] or "[]")
                except (TypeError, json.JSONDecodeError):
                    raw_models = []
                for model in raw_models if isinstance(raw_models, list) else []:
                    model = str(model or "").strip()
                    if _norm_model(model):
                        spec_models.setdefault(_norm_model(model), model)

            audit = self._entry_audit(code)
            entry_series_names = {
                str(group.get("series") or "")
                for group in ((audit or {}).get("details") or {}).get("series") or []
                if str(group.get("series") or "")
            }
            entry_models = {
                _norm_model(item["model"]): item["model"]
                for item in self._entry_models(code) if _norm_model(item["model"])
            }
            all_series = spec_series_names | entry_series_names
            all_models = dict(spec_models)
            all_models.update({key: value for key, value in entry_models.items()
                               if key not in all_models})
            if region == "jp":
                price_ok = self.db.kakaku_status_counts(task="price", brand=code)
                review_ok = self.db.kakaku_status_counts(task="review", brand=code)
                price_models = int(price_ok.get("ok", 0))
                review_models = int(review_ok.get("ok", 0))
            else:
                price_models = self._retail_ok_model_count(region, code, "price")
                review_models = self._retail_ok_model_count(region, code, "review")
            out.append({
                "code": code,
                "name": self._brand_name(code, region),
                "brand_name": (series_rows[0]["brand_name"] if series_rows else
                                self._brand_name(code, region)),
                "series_count": max(len(all_series), len(series_rows)),
                "model_count": len(all_models),
                "spec_series_count": len(series_rows),
                "spec_model_count": len(spec_models),
                "entry_series_count": (audit["discovered_series_count"] if audit else None),
                "entry_model_count": (audit["discovered_model_count"] if audit else None),
                "entry_expected_series_count": (audit["expected_series_count"] if audit else None),
                "entry_expected_model_count": (audit["expected_model_count"] if audit else None),
                "entry_termination_reason": (audit["termination_reason"] if audit else ""),
                "entry_audit_captured_at": (audit["captured_at"] if audit else ""),
                "latest_week": self.db.latest_week(code),
                "price_models": price_models,
                "review_models": review_models,
            })
        return out

    # ---------------- 型号（入口发现 + SPEC + 产品映射）----------------

    def _retail_product_for_model(self, region: str, brand: str,
                                  model: str, variants: list[str] | None = None):
        region = self._validate_region(region)
        allowed = sorted(self._retail_sites(region))
        if not allowed:
            return None
        candidates: list[str] = []
        for value in [model, *(variants or [])]:
            raw = str(value or "").strip()
            for candidate in (raw, _norm_model(raw)):
                if candidate and candidate not in candidates:
                    candidates.append(candidate)
        if not candidates:
            return None
        marks = ",".join("?" for _ in allowed)
        # 优先使用已绑定任务状态的商品，且站点必须是当前地区启用的零售站。
        for candidate in candidates:
            for task in ("price", "review"):
                row = self.db.conn.execute(
                    "SELECT r.product_id FROM retail_task_status r "
                    f"WHERE r.region=? AND r.task=? AND r.brand=? "
                    f"AND UPPER(r.model)=UPPER(?) AND r.product_id IS NOT NULL "
                    f"AND r.site_code IN ({marks}) ORDER BY r.id DESC LIMIT 1",
                    (region, task, brand, candidate, *allowed),
                ).fetchone()
                if row and row["product_id"]:
                    product = self._product_allowed(int(row["product_id"]), region, brand)
                    if product is not None:
                        return product
        # 没有状态记录时仍只允许配置内站点，并要求状态表存在品牌关联，
        # 防止同型号跨品牌/跨地区误绑定。
        for candidate in candidates:
            for product in self.db.products_by_model(candidate):
                if product["site_code"] not in set(allowed):
                    continue
                checked = self._product_allowed(int(product["id"]), region, brand)
                if checked is not None:
                    return checked
        return None

    def _jp_product_for_model(self, brand: str, model: str, item_id: str = "",
                              variants: list[str] | None = None):
        candidates: list[str] = []
        for value in [model, *(variants or [])]:
            raw = str(value or "").strip()
            for candidate in (raw, _norm_model(raw)):
                if candidate and candidate not in candidates:
                    candidates.append(candidate)
        if item_id:
            product = self.db.find_product("kakaku_jp", item_id)
            if product is not None:
                checked = self._product_allowed(int(product["id"]), "jp", brand)
                if checked is not None:
                    return checked
        for candidate in candidates:
            for product in self.db.products_by_model(candidate, "kakaku_jp"):
                checked = self._product_allowed(int(product["id"]), "jp", brand)
                if checked is not None:
                    return checked
        return None

    def models(self, brand: str = "", year: int | None = None,
               limit: int = 500, offset: int = 0,
               region: str = "jp") -> dict[str, Any]:
        """型号清单；入口发现与 SPEC 成功状态合并，价格/评价按地区补充。"""
        region = self._validate_region(region)
        selected_brand = self._validate_brand(region, brand)
        brands = [selected_brand] if selected_brand else self._brand_codes(region)
        release: dict[tuple[str, str], dict[str, Any]] = {}
        if region == "jp":
            for code in brands:
                path = self._data_file(f"发售日_{code}.json")
                if not os.path.exists(path):
                    continue
                try:
                    with open(path, encoding="utf-8") as handle:
                        raw = json.load(handle)
                except (OSError, json.JSONDecodeError):
                    continue
                for model, info in (raw or {}).items():
                    if isinstance(info, dict):
                        release[(code, _norm_model(model))] = info

        grouped: dict[tuple[str, str], dict[str, Any]] = {}
        entry_audits: dict[str, dict[str, Any]] = {}
        entry_counts: dict[str, dict[str, Any]] = {}
        spec_series_total = 0
        entry_series_total = 0
        series_keys: set[tuple[str, str]] = set()

        def add_occurrence(code: str, model: str, series: str, url: str,
                           source: str, spec_status: str,
                           series_identity: str = "") -> None:
            normalized = _norm_model(model)
            model = str(model or "").strip()
            if not normalized or not model:
                return
            key = (code, normalized)
            record = grouped.setdefault(key, {
                "brand": code,
                "brand_name": self._brand_name(code, region),
                "model": model,
                "series": [],
                "urls": [],
                "entry_urls": [],
                "sources": set(),
                "spec_statuses": [],
                "entry_series": [],
                "model_variants": [],
            })
            record["sources"].add(source)
            if model not in record["model_variants"]:
                record["model_variants"].append(model)
            if series:
                if series not in record["series"]:
                    record["series"].append(series)
                series_keys.add((code, series_identity or series))
            if url and url not in record["urls"]:
                record["urls"].append(url)
            if source == "entry_audit" and url and url not in record["entry_urls"]:
                record["entry_urls"].append(url)
            if source == "entry_audit" and series and series not in record["entry_series"]:
                record["entry_series"].append(series)
            if spec_status and spec_status not in record["spec_statuses"]:
                record["spec_statuses"].append(spec_status)

        for code in brands:
            audit = self._entry_audit(code)
            if audit:
                entry_audits[code] = audit
                entry_counts[code] = {
                    key: audit.get(key)
                    for key in ("expected_series_count", "discovered_series_count",
                                "expected_model_count", "discovered_model_count",
                                "termination_reason", "requested_url", "captured_at")
                }
                entry_series_total += int(audit.get("discovered_series_count") or 0)
            status_map = self._series_status_map(code)
            series_rows = self.db.spec_series_list(code)
            spec_series_total += len(series_rows)
            for row in series_rows:
                internal_series = str(row["series"] or "")
                series = display_series_name(internal_series)
                try:
                    series_models = json.loads(row["models"] or "[]")
                except (TypeError, json.JSONDecodeError):
                    series_models = []
                status = status_map.get(internal_series, "available")
                for model in series_models if isinstance(series_models, list) else []:
                    add_occurrence(code, str(model or ""), series,
                                   str(row["url"] or ""), "spec_series", status,
                                   internal_series)
            for entry in self._entry_models(code):
                add_occurrence(code, entry["model"], entry["series"], entry["url"],
                               "entry_audit", "entry_only")

        items: list[dict[str, Any]] = []
        status_priority = {"success": 5, "available": 4, "failed": 3,
                           "empty": 2, "entry_only": 1, "unknown": 0}
        for (code, normalized), record in grouped.items():
            info = release.get((code, normalized)) or {}
            ryear = info.get("year")
            # CA 等线没有日本发售日映射，不能用默认年份过滤入口型号。
            if region == "jp" and year is not None and ryear != year:
                continue
            if region == "jp":
                product = self._jp_product_for_model(
                    code, record["model"], str(info.get("item_id") or ""),
                    record.get("model_variants"))
            else:
                product = self._retail_product_for_model(
                    region, code, record["model"], record.get("model_variants"))
            statuses = record["spec_statuses"]
            best_status = max(statuses, key=lambda value: status_priority.get(value, 0)) \
                if statuses else "unknown"
            sources = record["sources"]
            source = "+".join(value for value in ("spec_series", "entry_audit")
                              if value in sources)
            if not source:
                source = "entry_audit"
            site_code = product["site_code"] if product is not None and "site_code" in product.keys() else ""
            site_name = product["site_name"] if product is not None and "site_name" in product.keys() else ""
            item_id = str(info.get("item_id") or "")
            if not item_id and product is not None:
                item_id = str(product["sku"] or "")
            items.append({
                "brand": code,
                "brand_name": record["brand_name"],
                "model": record["model"],
                "model_normalized": normalized,
                "model_variants": record.get("model_variants", [record["model"]]),
                "series": " | ".join(record["series"]),
                "series_list": record["series"],
                "release": info.get("release", ""),
                "release_year": ryear,
                "item_id": item_id,
                "product_id": int(product["id"]) if product is not None else None,
                "size": (product["size"] if product is not None else "") or "",
                "source": source,
                "source_url": " | ".join(record["urls"]),
                "entry_url": " | ".join(record["entry_urls"]),
                "entry_discovered": "entry_audit" in sources,
                "entry_series_list": record["entry_series"],
                "spec_status": best_status,
                "site_code": site_code or "",
                "site_name": site_name or "",
            })

        order = {code: i for i, code in enumerate(self._brand_codes(region))}
        items.sort(key=lambda x: (order.get(x["brand"], 99), x["series"], x["model"]))
        page = _page(items, limit, offset)
        page.update({
            "series_total": entry_series_total if entry_audits else len(series_keys),
            "spec_series_total": spec_series_total,
            "entry_series_total": entry_series_total if entry_audits else None,
            "model_total": len(items),
            "entry_audit": entry_audits,
            "entry_counts": entry_counts,
        })
        return page

    # ---------------- SPEC 宽表 ----------------

    def spec(self, brand: str, with_release: bool = True,
             region: str = "jp") -> dict[str, Any]:
        """SPEC 宽表：日本可附发售日，CA 直接返回官网规格快照。"""
        region = self._validate_region(region)
        brand = self._validate_brand(region, brand, required=True)
        release_map = {}
        if with_release and region == "jp":
            path = self._data_file(f"发售日_{brand}.json")
            release_map = load_release_map(path if os.path.exists(path) else None)
        models, rows = spec_collect(self.db, brand, release_map)
        return {
            "brand": brand,
            "brand_name": self._brand_name(brand, region),
            "week": self.db.latest_week(brand),
            "models": models,
            "rows": [
                {"category": cat, "item_ja": item, "item_zh": zh,
                 "values": {model: vals.get(model, "") for model in models}}
                for cat, item, zh, vals in rows
            ],
        }

    # ---------------- 价格 ----------------

    def _retail_prices(self, region: str, brand: str = "", model: str = "",
                       limit: int = 500, offset: int = 0) -> dict[str, Any]:
        allowed = sorted(self._retail_sites(region))
        if not allowed:
            return _page([], limit, offset)
        marks = ",".join("?" for _ in allowed)
        sql = (
            "SELECT r.* FROM retail_task_status r "
            "JOIN (SELECT region, task, site_code, brand, model, MAX(id) id "
            "      FROM retail_task_status WHERE region=? AND task='price' "
            f"      AND site_code IN ({marks}) "
            "      GROUP BY region, task, site_code, brand, model) last "
            " ON last.id=r.id WHERE r.region=? AND r.task='price' "
            f"AND r.site_code IN ({marks})"
        )
        args: list[Any] = [region, *allowed, region, *allowed]
        if brand:
            sql += " AND r.brand=?"
            args.append(brand)
        if model:
            sql += " AND UPPER(r.model)=UPPER(?)"
            args.append(model)
        sql += " ORDER BY r.brand, r.model, r.site_code"
        groups: dict[tuple[str, str], list[Any]] = {}
        for row in self.db.conn.execute(sql, args):
            groups.setdefault((row["brand"], row["model"]), []).append(row)

        items: list[dict[str, Any]] = []
        for (brand_code, model_name), rows in sorted(groups.items()):
            priced = [r for r in rows if r["price"] is not None]
            low = min(priced, key=lambda r: float(r["price"])) if priced else None
            latest = max(rows, key=lambda r: (r["captured_at"] or "", r["id"]))
            shops = []
            for row in rows:
                if row["price"] is None:
                    continue
                shops.append({
                    "shop": row["site_name"] or row["site_code"],
                    "site_code": row["site_code"],
                    "price": row["price"],
                    "currency": row["currency"] or region.upper(),
                    "is_lowest": bool(low and float(row["price"]) == float(low["price"])),
                })
            raw_pid = (low or latest)["product_id"]
            product = (self._product_allowed(int(raw_pid), region, brand_code)
                       if raw_pid else None)
            pid = int(product["id"]) if product is not None else None
            items.append({
                "brand": brand_code,
                "brand_name": latest["brand_name"] or self._brand_name(brand_code, region),
                "model": model_name,
                "item_id": (low or latest)["item_id"] or "",
                "product_id": pid,
                "size": (low or latest)["size"] or "",
                "release": "",
                "release_year": None,
                "status": "ok" if priced else latest["status"],
                "lowest": ({"price": low["price"], "currency": low["currency"] or region.upper(),
                            "captured_at": low["captured_at"]} if low else None),
                "shops": shops,
                "captured_at": latest["captured_at"],
            })
        return _page(items, limit, offset)

    def prices(self, brand: str = "", model: str = "", year: int | None = None,
               limit: int = 500, offset: int = 0,
               region: str = "jp") -> dict[str, Any]:
        """价格清单：JP 为 kakaku 店铺价，CA 为零售站商品快照。"""
        region = self._validate_region(region)
        brand = self._validate_brand(region, brand)
        if region != "jp":
            return self._retail_prices(region, brand, model, limit, offset)
        sql = ("SELECT k.brand, k.brand_name, k.model, k.item_id, k.product_id,"
               " k.size, k.release_text, k.release_year, k.status, k.captured_at"
               " FROM kakaku_task_status k"
               " JOIN (SELECT brand, model, MAX(id) id FROM kakaku_task_status"
               "       WHERE task='price' GROUP BY brand, model) last"
               "   ON last.id = k.id"
               " WHERE k.task='price'")
        args: list = []
        if brand:
            sql += " AND k.brand=?"
            args.append(brand)
        if model:
            sql += " AND UPPER(k.model)=UPPER(?)"
            args.append(model)
        if year is not None:
            sql += " AND k.release_year=?"
            args.append(year)
        sql += " ORDER BY k.brand, k.model"

        items: list[dict[str, Any]] = []
        for row in self.db.conn.execute(sql, args):
            pid = row["product_id"]
            product = (self._product_allowed(int(pid), "jp", row["brand"])
                       if pid else None)
            pid = int(product["id"]) if product is not None else None
            shops = []
            lowest = None
            if pid:
                shops = [{"shop": r["shop"], "price": r["price"],
                          "currency": r["currency"], "is_lowest": bool(r["is_lowest"])}
                         for r in self.db.latest_shop_prices(pid)]
                hist = self.db.price_history(pid, limit=1)
                if hist:
                    lowest = {"price": hist[0]["price"],
                              "currency": hist[0]["currency"],
                              "captured_at": hist[0]["captured_at"]}
            items.append({
                "brand": row["brand"], "brand_name": row["brand_name"],
                "model": row["model"], "item_id": row["item_id"],
                "product_id": pid, "size": row["size"] or "",
                "release": row["release_text"] or "",
                "release_year": row["release_year"], "status": row["status"],
                "lowest": lowest, "shops": shops, "captured_at": row["captured_at"],
            })
        return _page(items, limit, offset)

    def price_history(self, product_id: int, shop: str = "",
                      limit: int = 200, region: str = "jp") -> dict[str, Any]:
        """价格历史；product_id 必须属于当前地区允许的站点。"""
        region = self._validate_region(region)
        product = self._product_allowed(int(product_id), region)
        if product is None:
            raise ValueError(f"商品 {product_id} 不属于地区 {region} 或未配置站点")
        if region != "jp":
            sql = ("SELECT ps.price, ps.list_price, ps.currency, ps.in_stock, "
                   "ps.captured_at, s.code AS site_code, s.name AS site_name "
                   "FROM price_snapshot ps JOIN product p ON p.id=ps.product_id "
                   "JOIN site s ON s.id=p.site_id WHERE ps.product_id=?")
            args: list[Any] = [product_id]
            if shop:
                sql += " AND (s.code=? OR s.name=?)"
                args.extend([shop, shop])
            sql += " ORDER BY ps.captured_at DESC, ps.id DESC LIMIT ?"
            args.append(limit)
            rows = self.db.conn.execute(sql, args).fetchall()
            overall = [{"price": r["price"], "currency": r["currency"],
                        "captured_at": r["captured_at"]} for r in rows]
            shops = [{"shop": r["site_name"] or r["site_code"],
                      "site_code": r["site_code"], "price": r["price"],
                      "currency": r["currency"], "is_lowest": False,
                      "captured_at": r["captured_at"]} for r in rows]
            return {"product_id": product_id, "overall": overall, "shops": shops}
        overall = [{"price": r["price"], "currency": r["currency"],
                    "captured_at": r["captured_at"]}
                   for r in self.db.price_history(product_id, limit=limit)]
        shops = [{"shop": r["shop"], "price": r["price"], "currency": r["currency"],
                  "is_lowest": bool(r["is_lowest"]), "captured_at": r["captured_at"]}
                 for r in self.db.shop_price_history(product_id, shop=shop, limit=limit)]
        return {"product_id": product_id, "overall": overall, "shops": shops}

    # ---------------- 周度价格趋势（价格曲线） ----------------

    def weekly_price_trend(self, brand: str = "", model: str = "",
                           weeks: int = 12,
                           region: str = "jp") -> dict[str, Any]:
        """周度价格趋势：每产品 × 每渠道 × 每周一条（取该周最后报价）。

        返回 {weeks:[...], products:[{product_id, brand, model, size,
                shops:[{shop, series:[{week, price, is_lowest}]}]}]}。
        weeks 为返回序列的完整周次列表（含无数据周，供前端补断点）。
        """
        region = self._validate_region(region)
        if region != "jp":
            raise ValueError("周度价格趋势目前仅支持日本线(kakaku)")
        brand_code = self._validate_brand(region, brand)
        # 数据库 price_shop_snapshot 里 brand 字段是展示名（Hisense/SONY/...），
        # API 传入的是 code（hisense_jp），需转换后再过滤。
        brand_display = BRAND_DISPLAY.get(brand_code, "")
        rows = self.db.weekly_price_series("kakaku_jp", weeks_back=0)
        if brand_display:
            rows = [r for r in rows if r["brand"] == brand_display]
        if model:
            rows = [r for r in rows if r["model"] == model]
        # 汇总所有出现过的周次（升序），裁剪到最近 weeks 个
        all_weeks = sorted({r["week"] for r in rows})
        if weeks and weeks > 0:
            all_weeks = all_weeks[-weeks:]
        week_set = set(all_weeks)
        rows = [r for r in rows if r["week"] in week_set]
        # 按 product_id 分组
        prods: dict[int, dict] = {}
        for r in rows:
            pid = r["product_id"]
            p = prods.setdefault(pid, {
                "product_id": pid, "brand": r["brand"], "model": r["model"],
                "size": r["size"] or "", "shops": {}})
            sh = p["shops"].setdefault(r["shop"], {"shop": r["shop"], "series": []})
            sh["series"].append({"week": r["week"], "price": r["price"],
                                 "is_lowest": bool(r["is_lowest"])})
        # 每个 shop 的 series 按 week 排序
        products = []
        for p in prods.values():
            shops_out = []
            for sh in p["shops"].values():
                sh["series"].sort(key=lambda x: x["week"])
                shops_out.append(sh)
            shops_out.sort(key=lambda x: x["shop"])
            p["shops"] = shops_out
            products.append(p)
        products.sort(key=lambda x: (x["brand"], x["model"]))
        return {"weeks": all_weeks, "products": products, "count": len(products)}

    # ---------------- 评论 ----------------

    def reviews(self, brand: str = "", model: str = "", product_id: int | None = None,
                limit: int = 50, offset: int = 0,
                region: str = "jp") -> dict[str, Any]:
        """评论分页；日本按 kakaku 商品，CA 按启用零售站商品。"""
        region = self._validate_region(region)
        brand = self._validate_brand(region, brand)
        pids: list[int] = []
        if product_id:
            product = self._product_allowed(int(product_id), region, brand)
            if product is None:
                raise ValueError(f"商品 {product_id} 不属于当前地区或品牌")
            pids = [int(product_id)]
        elif model:
            for product in self.db.products_by_model(
                    model, "kakaku_jp" if region == "jp" else ""):
                checked = self._product_allowed(int(product["id"]), region, brand)
                if checked is not None:
                    pids.append(int(product["id"]))
        elif region == "jp":
            sql = ("SELECT DISTINCT product_id FROM kakaku_task_status"
                   " WHERE task='review' AND product_id IS NOT NULL")
            args: list = []
            if brand:
                sql += " AND brand=?"
                args.append(brand)
            for row in self.db.conn.execute(sql, args):
                product = self._product_allowed(int(row["product_id"]), region, brand)
                if product is not None:
                    pids.append(int(row["product_id"]))
        else:
            allowed = sorted(self._retail_sites(region))
            if allowed:
                marks = ",".join("?" for _ in allowed)
                sql = ("SELECT DISTINCT product_id FROM retail_task_status "
                       f"WHERE region=? AND task='review' AND product_id IS NOT NULL "
                       f"AND site_code IN ({marks})")
                args = [region, *allowed]
                if brand:
                    sql += " AND brand=?"
                    args.append(brand)
                for row in self.db.conn.execute(sql, args):
                    product = self._product_allowed(int(row["product_id"]), region, brand)
                    if product is not None:
                        pids.append(int(row["product_id"]))
        pids = sorted(set(pids))
        if not pids:
            return _page([], limit, offset)

        marks = ",".join("?" for _ in pids)
        total_row = self.db.conn.execute(
            f"SELECT COUNT(*) n FROM review WHERE product_id IN ({marks})", pids
        ).fetchone()
        rows = self.db.conn.execute(
            "SELECT r.*, p.brand, p.model, p.size, p.sku, "
            "s.code AS site_code, s.name AS site_name "
            "FROM review r JOIN product p ON p.id=r.product_id "
            "JOIN site s ON s.id=p.site_id "
            f"WHERE r.product_id IN ({marks}) "
            "ORDER BY r.review_date DESC, r.id DESC LIMIT ? OFFSET ?",
            (*pids, limit, offset),
        ).fetchall()
        items = []
        for row in rows:
            key = str(row["review_key"] or "")
            source = (row["site_name"] or row["site_code"] or "") if region != "jp" else (
                "レビュー" if key.startswith("ReviewCD=")
                else "クチコミ" if key.startswith("bbs:") else "")
            items.append({
                "product_id": row["product_id"], "brand": row["brand"],
                "model": row["model"], "size": row["size"] or "",
                "item_id": row["sku"], "review_key": row["review_key"],
                "source": source, "rating": row["rating"],
                "title": row["title"] or "", "body": row["body"] or "",
                "author": row["author"] or "", "review_date": row["review_date"] or "",
                "review_url": row["review_url"] or "", "translation": "",
                "ai_pros": "", "ai_cons": "",
            })
        return {"items": items, "total": int(total_row["n"]) if total_row else 0,
                "limit": limit, "offset": offset}

    # ---------------- 采集状态 ----------------

    def status(self, task: str = "", brand: str = "", state: str = "",
               week: str = "", limit: int = 500, offset: int = 0,
               region: str = "jp") -> dict[str, Any]:
        """采集状态：按地区组合 SPEC 与对应价格/评价状态。"""
        region = self._validate_region(region)
        brand = self._validate_brand(region, brand)
        if task not in {"", "spec", "price", "review", "release"}:
            raise ValueError(f"不支持的状态任务: {task}")
        if region != "jp" and task == "release":
            raise ValueError("非日本线没有 release 状态")
        allowed_brands = set(self._brand_codes(region))
        spec_items: list[dict[str, Any]] = []
        if not task or task == "spec":
            spec_rows = self.db.spec_series_status_list(
                brand=brand, captured_week=week, status=state)
            spec_items = [{
                "kind": "spec", "brand": r["brand"],
                "series": display_series_name(r["series"]),
                "week": r["captured_week"], "status": r["status"],
                "status_source": r["status_source"], "model_expected": r["model_expected"],
                "model_ok": r["model_ok"], "row_count": r["row_count"],
                "error_kind": r["error_kind"] or "", "message": r["message"] or "",
                "finished_at": r["finished_at"],
            } for r in spec_rows if r["brand"] in allowed_brands]

        if region == "jp":
            kak_rows = self.db.kakaku_status_list(task=task, brand=brand, status=state,
                                                  limit=5000)
            retail_items = [{
                "kind": "kakaku", "task": r["task"], "brand": r["brand"],
                "model": r["model"], "item_id": r["item_id"] or "",
                "status": r["status"], "release": r["release_text"] or "",
                "release_year": r["release_year"], "row_count": r["row_count"],
                "new_count": r["new_count"], "message": r["message"] or "",
                "captured_at": r["captured_at"],
            } for r in kak_rows if r["brand"] in allowed_brands]
            items = spec_items if task == "spec" else (
                spec_items + retail_items if not task else retail_items)
            def kakaku_counts(task_name: str) -> dict[str, int]:
                result: dict[str, int] = {}
                for code in ([brand] if brand else self._brand_codes(region)):
                    for key, value in self.db.kakaku_status_counts(
                            task=task_name, brand=code).items():
                        result[key] = result.get(key, 0) + int(value)
                return result

            return {
                "counts": {
                    "price": kakaku_counts("price"),
                    "review": kakaku_counts("review"),
                },
                **_page(items, limit, offset),
            }

        allowed_sites = sorted(self._retail_sites(region))
        retail_rows: list[Any] = []
        for site_code in allowed_sites:
            retail_rows.extend(self.db.retail_status_list(
                region=region, task=task if task in {"price", "review"} else "",
                site_code=site_code, brand=brand, status=state, limit=5000,
            ))
        retail_rows.sort(key=lambda row: (row["captured_at"] or "", row["id"]), reverse=True)
        retail_items = [{
            "kind": "retail", "region": r["region"], "task": r["task"],
            "site_code": r["site_code"], "site_name": r["site_name"] or r["site_code"],
            "brand": r["brand"], "brand_name": r["brand_name"] or self._brand_name(r["brand"], region),
            "model": r["model"], "item_id": r["item_id"] or "",
            "product_id": r["product_id"], "status": r["status"],
            "size": r["size"] or "", "price": r["price"],
            "currency": r["currency"] or "", "row_count": r["row_count"],
            "new_count": r["new_count"], "message": r["message"] or "",
            "captured_at": r["captured_at"],
        } for r in retail_rows if r["brand"] in allowed_brands]
        if task == "spec":
            items = spec_items
        elif task:
            items = retail_items
        else:
            items = spec_items + retail_items
        def retail_counts(task_name: str) -> dict[str, int]:
            result: dict[str, int] = {}
            for site_code in allowed_sites:
                for key, value in self.db.retail_status_counts(
                        region=region, task=task_name, site_code=site_code,
                        brand=brand).items():
                    result[key] = result.get(key, 0) + int(value)
            return result

        return {
            "counts": {
                "price": retail_counts("price"),
                "review": retail_counts("review"),
            },
            **_page(items, limit, offset),
        }
