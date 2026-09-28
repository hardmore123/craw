"""导出：把库里的 SPEC / 评价 / 价格生成 CSV 或 XLSX 字节流。"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime

from .. import spec_export
from ..db import Database
from ..price_export import PriceRecord, write_csv as price_csv, write_xlsx as price_xlsx
from ..price_export_ca import CaPriceRecord
from ..price_export_ca import write_csv as ca_price_csv, write_xlsx as ca_price_xlsx
from ..regions_config import get_region
from ..review_export import (NULL_HOOKS, ReviewRecord,
                            write_csv as review_csv, write_xlsx as review_xlsx)

CONTENT_TYPES = {
    "csv": "text/csv; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

BRAND_DISPLAY = {
    "hisense_jp": "Hisense", "sony_jp": "SONY", "regza_jp": "REGZA",
    "panasonic_jp": "Panasonic", "tcl_jp": "TCL", "sharp_jp": "SHARP",
}
BRAND_ZH = {
    "hisense_jp": "海信", "sony_jp": "索尼", "regza_jp": "REGZA",
    "panasonic_jp": "松下", "tcl_jp": "TCL", "sharp_jp": "夏普",
}


def _brand_name(code: str, region: str = "jp") -> str:
    if code in BRAND_DISPLAY:
        return BRAND_DISPLAY[code]
    cfg = get_region(region)
    if cfg:
        for brand in cfg.brands:
            if brand.code == code:
                return brand.name
    return code


def _emit(write_fn, fmt: str) -> bytes:
    """用 write_fn(临时路径) 生成文件并读回字节，随后删除临时文件。"""
    suffix = "." + fmt
    fd, tmp = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    try:
        write_fn(tmp)
        with open(tmp, "rb") as handle:
            return handle.read()
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _release_map(db_path: str | None, brand: str) -> dict[str, str]:
    from .. import config
    path = os.path.join(config.DATA_DIR, f"发售日_{brand}.json")
    return spec_export.load_release_map(path if os.path.exists(path) else None)


def export_spec(db: Database, brand: str, fmt: str, db_path: str | None,
                region: str = "jp") -> tuple[bytes, str, str]:
    rm = _release_map(db_path, brand) if region == "jp" else {}
    stamp = datetime.now().strftime("%Y%m%d")
    name = f"SPEC_{BRAND_ZH.get(brand, _brand_name(brand, region))}_{stamp}.{fmt}"
    if fmt == "xlsx":
        data = _emit(lambda p: spec_export.export_xlsx(db, brand, p, rm), fmt)
    else:
        data = _emit(lambda p: spec_export.export_csv(db, brand, p, rm), fmt)
    return data, name, CONTENT_TYPES[fmt]


def _review_records(db: Database, brand: str, region: str = "jp") -> list[ReviewRecord]:
    if region == "jp":
        sql = (
            "SELECT r.*, p.brand, p.model, p.size, p.sku FROM review r"
            " JOIN product p ON p.id=r.product_id"
            " JOIN site s ON s.id=p.site_id WHERE s.code='kakaku_jp'"
        )
        args: list = []
        if brand:
            sql += " AND EXISTS (SELECT 1 FROM kakaku_task_status k"
            sql += "  WHERE k.product_id=p.id AND k.brand=?)"
            args.append(brand)
        sql += " ORDER BY p.model, r.review_date DESC"
        country = "Japan"
    else:
        cfg = get_region(region)
        sites = [s.site for s in cfg.retail_sites] if cfg else []
        if not sites:
            return []
        marks = ",".join("?" for _ in sites)
        sql = (
            "SELECT r.*, p.brand, p.model, p.size, p.sku, s.name AS site_name, "
            "(SELECT t.brand_name FROM retail_task_status t "
            " WHERE t.product_id=p.id AND t.region=? AND t.task='review' "
            " ORDER BY t.id DESC LIMIT 1) AS retail_brand_name "
            "FROM review r JOIN product p ON p.id=r.product_id "
            "JOIN site s ON s.id=p.site_id "
            f"WHERE s.code IN ({marks}) "
            "AND EXISTS (SELECT 1 FROM retail_task_status t2 "
            " WHERE t2.product_id=p.id AND t2.region=? AND t2.task='review'"
        )
        args = [region, *sites, region]
        if brand:
            sql += " AND t2.brand=?"
            args.append(brand)
        sql += ") ORDER BY p.model, r.review_date DESC"
        country = cfg.name if cfg else region

    out: list[ReviewRecord] = []
    for row in db.conn.execute(sql, args):
        key = str(row["review_key"] or "")
        if region == "jp":
            source = ("レビュー" if key.startswith("ReviewCD=")
                      else "クチコミ" if key.startswith("bbs:") else "")
            channel = "kakaku.com"
            display = _brand_name(row["brand"] or "", region)
        else:
            source = row["site_name"] or ""
            channel = source
            display = row["retail_brand_name"] or row["brand"] or ""
        out.append(ReviewRecord(
            country=country, brand=display, model=row["model"] or "",
            channel=channel, size=row["size"] or "",
            rating=("" if row["rating"] is None else row["rating"]),
            title=row["title"] or "", body=row["body"] or "", helpful="",
            review_time=row["review_date"] or "", review_url=row["review_url"] or "",
            image_count=row["image_count"] or 0, image_urls=row["image_urls"] or "",
            has_video=("是" if row["has_video"] else "否"),
            video_urls=row["video_urls"] or "", item_id=row["sku"] or "",
        ))
    return out


def export_reviews(db: Database, brand: str, fmt: str,
                   translate: bool = False, engine: str = "nllb",
                   region: str = "jp") -> tuple[bytes, str, str]:
    records = _review_records(db, brand, region)
    if translate and records:
        try:
            from ..review_translation import build_translation_hooks
            _hooks, translator = build_translation_hooks(engine=engine)
            translations = translator.translate_many([r.body for r in records])
            for record, translation in zip(records, translations):
                if not record.translation:
                    record.translation = translation
        except Exception:
            pass
    stamp = datetime.now().strftime("%Y%m%d")
    tag = BRAND_ZH.get(brand, _brand_name(brand, region)) if brand else "全部品牌"
    name = f"网评汇总_{tag}_{stamp}.{fmt}"
    write = review_xlsx if fmt == "xlsx" else review_csv
    return _emit(lambda p: write(p, records, NULL_HOOKS), fmt), name, CONTENT_TYPES[fmt]


def _price_records(db: Database, brand: str,
                   region: str = "jp") -> list[PriceRecord] | list[CaPriceRecord]:
    if region != "jp":
        cfg = get_region(region)
        sites = [s.site for s in cfg.retail_sites] if cfg else []
        if not sites:
            return []
        sql = (
            "SELECT r.* FROM retail_task_status r "
            "JOIN (SELECT region, task, site_code, brand, model, MAX(id) id "
            "      FROM retail_task_status WHERE region=? AND task='price' "
            "      GROUP BY region, task, site_code, brand, model) last ON last.id=r.id "
            "WHERE r.region=? AND r.task='price'"
        )
        args: list = [region, region]
        if brand:
            sql += " AND r.brand=?"
            args.append(brand)
        sql += " ORDER BY r.brand, r.model, r.site_code"
        groups: dict[tuple[str, str], list] = {}
        for row in db.conn.execute(sql, args):
            groups.setdefault((row["brand"], row["model"]), []).append(row)
        out: list[CaPriceRecord] = []
        country = cfg.name if cfg else region
        currency = cfg.currency if cfg else ""
        for (code, model), rows in sorted(groups.items()):
            latest = max(rows, key=lambda r: (r["captured_at"] or "", r["id"]))
            out.append(CaPriceRecord(
                country=country,
                brand=latest["brand_name"] or _brand_name(code, region),
                model=model, currency=currency,
                retail_prices={r["site_code"]: r["price"] for r in rows
                               if r["price"] is not None},
                captured_at=latest["captured_at"] or "",
            ))
        return out

    sql = (
        "SELECT k.brand, k.model, k.item_id, k.product_id, k.size,"
        " k.release_text, k.captured_at FROM kakaku_task_status k"
        " JOIN (SELECT brand, model, MAX(id) id FROM kakaku_task_status"
        "       WHERE task='price' GROUP BY brand, model) last ON last.id=k.id"
        " WHERE k.task='price'"
    )
    args: list = []
    if brand:
        sql += " AND k.brand=?"
        args.append(brand)
    sql += " ORDER BY k.brand, k.model"
    out: list[PriceRecord] = []
    for row in db.conn.execute(sql, args):
        pid = row["product_id"]
        shops: dict[str, float] = {}
        lowest_shop = ""
        if pid:
            for shop_row in db.latest_shop_prices(int(pid)):
                shops[shop_row["shop"]] = shop_row["price"]
                if shop_row["is_lowest"]:
                    lowest_shop = shop_row["shop"]
        lowest = ""
        if pid:
            hist = db.price_history(int(pid), limit=1)
            if hist and hist[0]["price"] is not None:
                lowest = int(hist[0]["price"])
        out.append(PriceRecord(
            region="Japan", brand=_brand_name(row["brand"] or "", region),
            model=row["model"] or "", size=row["size"] or "",
            release=(row["release_text"] or "").replace(" 発売", "").replace("発売", ""),
            currency="JPY", lowest_price=lowest, shop_prices=shops,
            lowest_shop=lowest_shop, item_id=row["item_id"] or "",
            url=(f"https://kakaku.com/item/{row['item_id']}/" if row["item_id"] else ""),
            captured_at=(row["captured_at"] or "").replace("T", " ")[:16],
        ))
    return out


def export_prices(db: Database, brand: str, fmt: str,
                  region: str = "jp") -> tuple[bytes, str, str]:
    records = _price_records(db, brand, region)
    stamp = datetime.now().strftime("%Y%m%d")
    if region == "jp":
        tag = BRAND_ZH.get(brand, brand) if brand else "全部品牌"
        name = f"价格监控_{tag}_{stamp}.{fmt}"
        write = price_xlsx if fmt == "xlsx" else price_csv
    else:
        cfg = get_region(region)
        tag = _brand_name(brand, region) if brand else "全部品牌"
        name = f"价格监控_{cfg.name if cfg else region}_{tag}_{stamp}.{fmt}"
        write = ca_price_xlsx if fmt == "xlsx" else ca_price_csv
    return _emit(lambda p: write(p, records, region) if region != "jp"
                 else write(p, records), fmt), name, CONTENT_TYPES[fmt]
