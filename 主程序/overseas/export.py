"""按目标表格格式导出：每条评价一行（宽表）。

列顺序严格对齐需求表：
  所属国家 | 品牌 | 型号 | 渠道 | 尺寸 | 网评分数(星级) | 网评标题 | 网评内容 |
  赞同数 | 评论时间 | 评论链接 | 图片数量 | 图片地址 | 是否有视频 | 视频地址

默认导出 CSV（标准库，Excel 可直接打开，UTF-8 BOM 防中文乱码）；
装了 openpyxl 时可导出 .xlsx。
"""
from __future__ import annotations

import csv
import os

from .db import Database
from .extract import t_size_inch
from .sites import SiteRegistry

# 统一采用 review_export 的通用 20 列模板（多站点/多地区共用）：
# 前 15 列与旧 Amazon 表一致，后 5 列为 翻译 / 提炼优点(通过AI) / 提炼缺点(通过AI) / 商品ID / 价格。
from .review_export import COLUMNS  # noqa: E402


def _rows(db: Database, site_code: str = "", limit: int = 100000):
    """一条评价一行，带上所属商品与站点信息。"""
    # 站点国家/渠道来自适配器（DB 未存，导出时按 code 查）
    site_info: dict[str, tuple[str, str]] = {}
    for code in SiteRegistry.codes():
        try:
            a = SiteRegistry.get(code)
            site_info[code] = (a.country, a.channel)
        except Exception:
            site_info[code] = ("", "")

    # 价格取该商品最近一次价格快照（子查询，按 captured_at 倒序取一条）
    sql = ("SELECT s.code AS site_code, p.brand, p.model, p.size, p.sku,"
           " r.rating, r.title, r.body, r.helpful_count, r.review_date,"
           " r.review_url, r.image_count, r.image_urls, r.has_video, r.video_urls,"
           " (SELECT ps.price FROM price_snapshot ps WHERE ps.product_id = p.id"
           "  ORDER BY ps.captured_at DESC LIMIT 1) AS last_price,"
           " (SELECT ps.currency FROM price_snapshot ps WHERE ps.product_id = p.id"
           "  ORDER BY ps.captured_at DESC LIMIT 1) AS last_currency"
           " FROM review r"
           " JOIN product p ON p.id = r.product_id"
           " JOIN site s ON s.id = p.site_id")
    args: list = []
    if site_code:
        sql += " WHERE s.code = ?"
        args.append(site_code)
    sql += " ORDER BY p.model, r.review_date DESC LIMIT ?"
    args.append(limit)

    for r in db.conn.execute(sql, args).fetchall():
        country, channel = site_info.get(r["site_code"], ("", ""))
        price = ""
        if r["last_price"] is not None:
            cur = r["last_currency"] or ""
            price = f"{cur}{r['last_price']}".strip()
        yield [
            country,
            r["brand"] or "",
            r["model"] or "",
            channel,
            t_size_inch(r["size"] or ""),      # 存量数据也统一为 X"
            "" if r["rating"] is None else r["rating"],
            r["title"] or "",
            r["body"] or "",
            "" if r["helpful_count"] is None else r["helpful_count"],
            r["review_date"] or "",
            r["review_url"] or "",
            r["image_count"] or 0,
            r["image_urls"] or "",
            "是" if r["has_video"] else "否",
            r["video_urls"] or "",
            # --- 扩展 5 列：Amazon 侧 AI 三列留空，商品ID=SKU(ASIN)，价格=最近快照 ---
            "",                                 # 翻译
            "",                                 # 提炼优点(通过AI)
            "",                                 # 提炼缺点(通过AI)
            r["sku"] or "",                     # 商品ID
            price,                              # 价格
        ]


def export_csv(db: Database, path: str, site_code: str = "", limit: int = 100000) -> int:
    n = 0
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    # utf-8-sig：Excel 打开 CSV 不乱码
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        for row in _rows(db, site_code, limit):
            w.writerow(row)
            n += 1
    return n


def export_xlsx(db: Database, path: str, site_code: str = "", limit: int = 100000) -> int:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
    except ImportError as e:
        raise RuntimeError("导出 xlsx 需要 openpyxl：pip install openpyxl") from e

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "网评汇总"
    # 表头样式（对齐需求表的橙色表头）
    fill = PatternFill("solid", fgColor="E38B29")
    font = Font(color="FFFFFF", bold=True)
    ws.append(COLUMNS)
    for c in ws[1]:
        c.fill = fill
        c.font = font
        c.alignment = Alignment(horizontal="center", vertical="center")
    n = 0
    for row in _rows(db, site_code, limit):
        ws.append(row)
        n += 1
    # 合理列宽（20 列：前 15 与旧表一致，后 5 为扩展列）
    widths = [10, 8, 16, 8, 10, 12, 22, 40, 8, 22, 30, 8, 40, 8, 30,
              40, 24, 24, 14, 12]
    for i, wdt in enumerate(widths, 1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = wdt
    ws.freeze_panes = "A2"
    wb.save(path)
    return n
