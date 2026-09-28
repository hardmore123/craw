"""存储层：SQLite 实现。

对外只暴露 Database 一个类，SQL 全部收在这里，
后续要迁 PostgreSQL 只需替换本文件（表结构见 schema.sql，已尽量用标准 SQL）。

时序语义（重要）：
- add_price / add_review_summary / add_ranking 都是 INSERT，不做 UPDATE，
  历史快照全部保留，才能算价格曲线和口碑趋势。
- add_reviews 用 INSERT OR IGNORE + UNIQUE(product_id, review_key) 实现增量去重。
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone

from . import config
from .models import (Product, PriceSnapshot, RankingItem, Review, ReviewSummary,
                     SpecItem, SpecSheet)

SCHEMA_VERSION = 7


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: str | None = None):
        self.path = path or config.DB_PATH
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=30.0)
        self.conn.row_factory = sqlite3.Row
        # WAL 提升并发读写；外键约束默认关闭，需显式打开
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA synchronous=NORMAL")

    # ---------------- 生命周期 ----------------
    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        try:
            self.conn.commit()
        finally:
            self.conn.close()

    # 旧库缺列补齐清单：{表: [(列名, 列定义), ...]}
    # CREATE TABLE IF NOT EXISTS 不会给已存在的表加列，早期建库的实例会缺列，
    # 必须显式 ALTER TABLE 补齐（如 product.size 曾导致 upsert_product 报错）。
    _COLUMN_MIGRATIONS: dict[str, list[tuple[str, str]]] = {
        "product": [("size", "TEXT")],
        "review": [
            ("review_url", "TEXT"),
            ("image_urls", "TEXT"),
            ("image_count", "INTEGER DEFAULT 0"),
            ("video_urls", "TEXT"),
            ("has_video", "INTEGER DEFAULT 0"),
        ],
    }

    def _migrate_columns(self) -> list[str]:
        """给已存在的表补齐 schema.sql 中新增的列。返回实际补加的 '表.列' 列表。"""
        added: list[str] = []
        for table, columns in self._COLUMN_MIGRATIONS.items():
            try:
                existing = {r["name"] for r in
                            self.conn.execute(f"PRAGMA table_info({table})")}
            except sqlite3.Error:
                continue
            if not existing:               # 表不存在，交给 schema.sql 建
                continue
            for name, ddl in columns:
                if name in existing:
                    continue
                try:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
                    added.append(f"{table}.{name}")
                except sqlite3.Error:
                    pass
        if added:
            self.conn.commit()
        return added

    def _migrate_retail_status(self) -> list[str]:
        """重建不完整的零售状态表，补齐非空 run_id 与真正的唯一键。

        早期 v5 表允许 run_id=NULL；SQLite 对 UNIQUE 中的 NULL 不去重，
        同一型号可能因此产生重复状态。ALTER TABLE 无法补非空/表级唯一约束，
        所以仅在检测到旧结构时创建新表并保留仍能关联到 crawl_run 的最新记录。
        """
        try:
            info = list(self.conn.execute("PRAGMA table_info(retail_task_status)"))
        except sqlite3.Error:
            return []
        if not info:
            return []
        columns = {row["name"] for row in info}
        required = {
            "run_id", "region", "task", "site_code", "site_name", "brand",
            "brand_name", "model", "item_id", "product_id", "status", "size",
            "price", "currency", "row_count", "new_count", "message", "captured_at",
        }
        run_not_null = any(row["name"] == "run_id" and row["notnull"] for row in info)
        unique_key = ("run_id", "task", "region", "site_code", "brand", "model")
        has_unique = False
        try:
            for index in self.conn.execute("PRAGMA index_list(retail_task_status)"):
                if not index["unique"]:
                    continue
                name = str(index["name"]).replace("'", "''")
                index_cols = [row["name"] for row in
                              self.conn.execute(f"PRAGMA index_info('{name}')")]
                if tuple(index_cols) == unique_key:
                    has_unique = True
                    break
        except sqlite3.Error:
            has_unique = False
        if required.issubset(columns) and run_not_null and has_unique:
            return []

        # 这些索引属于旧表；先释放名称，避免新表建索引时冲突。
        self.conn.execute("DROP INDEX IF EXISTS idx_retail_status_task_site")
        self.conn.execute("DROP INDEX IF EXISTS idx_retail_status_brand_model")
        legacy = "retail_task_status_legacy_" + datetime.now(
            timezone.utc).strftime("%Y%m%d%H%M%S%f")
        target = [
            "run_id", "region", "task", "site_code", "site_name", "brand",
            "brand_name", "model", "item_id", "product_id", "status", "size",
            "price", "currency", "row_count", "new_count", "message", "captured_at",
        ]
        try:
            self.conn.execute(f"ALTER TABLE retail_task_status RENAME TO {legacy}")
            self.conn.execute("""
                CREATE TABLE retail_task_status (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id      INTEGER NOT NULL REFERENCES crawl_run(id) ON DELETE CASCADE,
                    region      TEXT NOT NULL,
                    task        TEXT NOT NULL CHECK (task IN ('price', 'review')),
                    site_code   TEXT NOT NULL,
                    site_name   TEXT,
                    brand       TEXT NOT NULL,
                    brand_name  TEXT,
                    model       TEXT NOT NULL,
                    item_id     TEXT,
                    product_id  INTEGER REFERENCES product(id) ON DELETE SET NULL,
                    status      TEXT NOT NULL,
                    size        TEXT,
                    price       REAL,
                    currency    TEXT,
                    row_count   INTEGER NOT NULL DEFAULT 0,
                    new_count   INTEGER NOT NULL DEFAULT 0,
                    message     TEXT,
                    captured_at TEXT NOT NULL,
                    UNIQUE (run_id, task, region, site_code, brand, model)
                )
            """)
            defaults = {
                "region": "''", "site_code": "''", "site_name": "''",
                "brand": "''", "brand_name": "''", "model": "''",
                "item_id": "''", "size": "''", "price": "NULL",
                "currency": "''", "row_count": "0", "new_count": "0",
                "message": "''", "captured_at": "CURRENT_TIMESTAMP",
            }
            expressions: list[str] = []
            for column in target:
                if column not in columns:
                    expressions.append(defaults.get(column, "NULL"))
                    continue
                ref = f'legacy."{column}"'
                if column == "task":
                    expressions.append(
                        f"CASE WHEN {ref} IN ('price','review') THEN {ref} ELSE 'price' END")
                elif column == "status":
                    expressions.append(
                        f"CASE WHEN {ref} IN ('ok','no_item','no_price','no_reviews',"
                        f"'summary_only','blocked','failed') THEN {ref} ELSE 'failed' END")
                elif column == "product_id":
                    expressions.append(
                        f"CASE WHEN {ref} IS NULL OR EXISTS "
                        f"(SELECT 1 FROM product p WHERE p.id={ref}) "
                        f"THEN {ref} ELSE NULL END")
                elif column == "captured_at":
                    expressions.append(f"COALESCE(NULLIF({ref},''), CURRENT_TIMESTAMP)")
                elif column in {"row_count", "new_count"}:
                    expressions.append(f"COALESCE({ref}, 0)")
                elif column in {"region", "site_code", "site_name", "brand",
                                "brand_name", "model", "item_id", "size",
                                "currency", "message"}:
                    expressions.append(f"COALESCE({ref}, '')")
                else:
                    expressions.append(ref)
            if "run_id" not in columns:
                where = "0"
            else:
                where = ("legacy.\"run_id\" IS NOT NULL AND EXISTS "
                         "(SELECT 1 FROM crawl_run cr WHERE cr.id=legacy.\"run_id\")")
            order = ' ORDER BY legacy."id" DESC' if "id" in columns else ""
            self.conn.execute(
                f"INSERT OR IGNORE INTO retail_task_status({','.join(target)}) "
                f"SELECT {','.join(expressions)} FROM {legacy} AS legacy WHERE {where}{order}")
            self.conn.execute(f"DROP TABLE {legacy}")
            self.conn.execute(
                "CREATE INDEX idx_retail_status_task_site "
                "ON retail_task_status(region, task, site_code, status)")
            self.conn.execute(
                "CREATE INDEX idx_retail_status_brand_model "
                "ON retail_task_status(region, brand, model, captured_at)")
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return ["retail_task_status"]

    def init_schema(self) -> int:
        """建表（幂等）并记录 schema 版本。返回当前版本号。"""
        sql_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")
        with open(sql_path, encoding="utf-8") as f:
            self.conn.executescript(f.read())
        self._migrate_columns()        # 旧库缺列补齐（幂等）
        self._migrate_retail_status()  # v5 旧表可能缺非空/唯一边界
        cur = self.conn.execute("SELECT MAX(version) AS v FROM schema_version")
        row = cur.fetchone()
        current_version = int(row["v"]) if row and row["v"] is not None else 0
        if current_version < SCHEMA_VERSION:
            self.conn.execute(
                "INSERT INTO schema_version(version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION, _now()))
        self.conn.commit()
        return self.schema_version()

    def schema_version(self) -> int:
        try:
            cur = self.conn.execute("SELECT MAX(version) AS v FROM schema_version")
            row = cur.fetchone()
            return int(row["v"]) if row and row["v"] is not None else 0
        except sqlite3.Error:
            return 0

    # ---------------- 站点 ----------------
    def upsert_site(self, code: str, name: str, base_url: str = "",
                    protection: str = "") -> int:
        self.conn.execute(
            "INSERT INTO site(code, name, base_url, protection, created_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(code) DO UPDATE SET name=excluded.name, "
            "base_url=excluded.base_url, protection=excluded.protection",
            (code, name, base_url, protection, _now()))
        self.conn.commit()
        row = self.conn.execute("SELECT id FROM site WHERE code=?", (code,)).fetchone()
        return int(row["id"])

    # ---------------- 商品 ----------------
    def upsert_product(self, p: Product, site_id: int | None = None) -> int:
        """按 (site_id, sku) upsert；非空字段才覆盖，避免用空值冲掉已有数据。"""
        if site_id is None:
            site_id = self.upsert_site(p.site_code, p.site_code)
        now = _now()
        self.conn.execute(
            "INSERT INTO product(site_id, sku, url, title, brand, model, category,"
            " size, first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(site_id, sku) DO UPDATE SET "
            " url=COALESCE(NULLIF(excluded.url,''), product.url),"
            " title=COALESCE(NULLIF(excluded.title,''), product.title),"
            " brand=COALESCE(NULLIF(excluded.brand,''), product.brand),"
            " model=COALESCE(NULLIF(excluded.model,''), product.model),"
            " category=COALESCE(NULLIF(excluded.category,''), product.category),"
            " size=COALESCE(NULLIF(excluded.size,''), product.size),"
            " last_seen=excluded.last_seen",
            (site_id, p.sku, p.url, p.title, p.brand, p.model, p.category,
             p.size, now, now))
        self.conn.commit()
        row = self.conn.execute(
            "SELECT id FROM product WHERE site_id=? AND sku=?", (site_id, p.sku)
        ).fetchone()
        return int(row["id"])

    def save_specs(self, product_id: int, specs: list[SpecItem]) -> int:
        if not specs:
            return 0
        now = _now()
        self.conn.executemany(
            "INSERT INTO product_spec(product_id, key, value, captured_at) "
            "VALUES (?,?,?,?) "
            "ON CONFLICT(product_id, key) DO UPDATE SET "
            " value=excluded.value, captured_at=excluded.captured_at",
            [(product_id, s.key, s.value, now) for s in specs if s.key])
        self.conn.commit()
        return len(specs)

    # ---------------- 时序：价格 ----------------
    def add_price(self, product_id: int, snap: PriceSnapshot) -> int:
        cur = self.conn.execute(
            "INSERT INTO price_snapshot(product_id, price, list_price, currency,"
            " in_stock, raw_text, captured_at) VALUES (?,?,?,?,?,?,?)",
            (product_id, snap.price, snap.list_price, snap.currency,
             None if snap.in_stock is None else int(snap.in_stock),
             snap.raw_text, _now()))
        self.conn.commit()
        return int(cur.lastrowid)

    # ---------------- 评价（增量去重）----------------
    def add_reviews(self, product_id: int, reviews: list[Review]) -> int:
        """写入评价，返回真正新增的条数。

        已存在的 review_key 不重复插入，但会「补全」此前为空的字段——
        修好选择器后重跑能修复历史空值，不用清库重采。
        """
        items = [r for r in reviews if r.review_key]
        if not items:
            return 0
        now = _now()
        known = self.existing_review_keys(product_id)
        fresh = [r for r in items if r.review_key not in known]
        stale = [r for r in items if r.review_key in known]

        if fresh:
            self.conn.executemany(
                "INSERT OR IGNORE INTO review(product_id, review_key, rating, title,"
                " body, author, review_date, verified, helpful_count,"
                " review_url, image_urls, image_count, video_urls, has_video,"
                " captured_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(product_id, r.review_key, r.rating, r.title, r.body, r.author,
                  r.review_date,
                  None if r.verified is None else int(r.verified),
                  r.helpful_count, r.review_url,
                  " | ".join(r.image_urls), r.image_count,
                  " | ".join(r.video_urls), int(r.has_video), now) for r in fresh])
        if stale:
            # 只补空：已有非空值不覆盖，避免把正确数据写坏；媒体字段有新值就更新
            self.conn.executemany(
                "UPDATE review SET"
                " rating=COALESCE(rating, ?),"
                " title=COALESCE(NULLIF(title,''), ?),"
                " body=COALESCE(NULLIF(body,''), ?),"
                " author=COALESCE(NULLIF(author,''), ?),"
                " review_date=COALESCE(NULLIF(review_date,''), ?),"
                " verified=COALESCE(verified, ?),"
                " helpful_count=COALESCE(helpful_count, ?),"
                " review_url=COALESCE(NULLIF(review_url,''), ?),"
                " image_urls=COALESCE(NULLIF(image_urls,''), ?),"
                " image_count=MAX(COALESCE(image_count,0), ?),"
                " video_urls=COALESCE(NULLIF(video_urls,''), ?),"
                " has_video=MAX(COALESCE(has_video,0), ?)"
                " WHERE product_id=? AND review_key=?",
                [(r.rating, r.title, r.body, r.author, r.review_date,
                  None if r.verified is None else int(r.verified),
                  r.helpful_count, r.review_url,
                  " | ".join(r.image_urls), r.image_count,
                  " | ".join(r.video_urls), int(r.has_video),
                  product_id, r.review_key) for r in stale])
        self.conn.commit()
        return len(fresh)

    def existing_review_keys(self, product_id: int) -> set[str]:
        """已入库的 review_key 集合，供增量场景提前止损（少翻页）。"""
        rows = self.conn.execute(
            "SELECT review_key FROM review WHERE product_id=?", (product_id,)).fetchall()
        return {r["review_key"] for r in rows}

    def add_review_summary(self, product_id: int, s: ReviewSummary) -> int:
        st = s.stars or {}
        cur = self.conn.execute(
            "INSERT INTO review_summary(product_id, avg_rating, total_count,"
            " star1, star2, star3, star4, star5, captured_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (product_id, s.avg_rating, s.total_count,
             st.get(1), st.get(2), st.get(3), st.get(4), st.get(5), _now()))
        self.conn.commit()
        return int(cur.lastrowid)

    def add_ranking(self, product_id: int, r: RankingItem) -> int:
        cur = self.conn.execute(
            "INSERT INTO ranking_snapshot(product_id, list_name, rank, captured_at) "
            "VALUES (?,?,?,?)", (product_id, r.list_name, r.rank, _now()))
        self.conn.commit()
        return int(cur.lastrowid)

    # ---------------- 任务审计 ----------------
    def start_run(self, scenario: str, site_code: str = "", params: dict | None = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO crawl_run(scenario, site_code, params, status, started_at) "
            "VALUES (?,?,?,?,?)",
            (scenario, site_code, json.dumps(params or {}, ensure_ascii=False),
             "running", _now()))
        self.conn.commit()
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, status: str, ok: int = 0, fail: int = 0,
                   message: str = "") -> None:
        self.conn.execute(
            "UPDATE crawl_run SET status=?, finished_at=?, ok_count=?, fail_count=?,"
            " message=? WHERE id=?",
            (status, _now(), ok, fail, message[:2000], run_id))
        self.conn.commit()

    def log_error(self, run_id: int | None, url: str, stage: str, kind: str,
                  message: str) -> None:
        self.conn.execute(
            "INSERT INTO crawl_error(run_id, url, stage, kind, message, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (run_id, url[:500], stage, kind, message[:1000], _now()))
        self.conn.commit()

    def save_raw_page(self, run_id: int | None, url: str, html: str,
                      status: int | None = None) -> str:
        """原始 HTML 落文件，DB 只记路径 + sha1。"""
        os.makedirs(config.RAW_DIR, exist_ok=True)
        sha1 = hashlib.sha1(html.encode("utf-8", "replace")).hexdigest()
        path = os.path.join(config.RAW_DIR, f"{sha1}.html")
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                f.write(html)
        self.conn.execute(
            "INSERT INTO raw_page(run_id, url, sha1, path, status, created_at) "
            "VALUES (?,?,?,?,?,?)", (run_id, url[:500], sha1, path, status, _now()))
        self.conn.commit()
        return path

    # ---------------- 查询（CLI 展示 / 导出）----------------
    def stats(self) -> dict:
        def one(sql: str) -> int:
            try:
                row = self.conn.execute(sql).fetchone()
            except sqlite3.Error:
                # 兼容尚未执行 init_schema 的空库/旧库，健康检查仍可返回。
                return 0
            return int(row[0]) if row and row[0] is not None else 0
        return {
            "sites": one("SELECT COUNT(*) FROM site"),
            "products": one("SELECT COUNT(*) FROM product"),
            "specs": one("SELECT COUNT(*) FROM product_spec"),
            "price_snapshots": one("SELECT COUNT(*) FROM price_snapshot"),
            "price_shop_snapshots": one("SELECT COUNT(*) FROM price_shop_snapshot"),
            "reviews": one("SELECT COUNT(*) FROM review"),
            "review_summaries": one("SELECT COUNT(*) FROM review_summary"),
            "kakaku_task_status": one("SELECT COUNT(*) FROM kakaku_task_status"),
            "retail_task_status": one("SELECT COUNT(*) FROM retail_task_status"),
            "spec_series": one("SELECT COUNT(*) FROM spec_series"),
            "spec_rows": one("SELECT COUNT(*) FROM spec_row"),
            "spec_untranslated": one("SELECT COUNT(*) FROM spec_untranslated"),
            "spec_series_status": one("SELECT COUNT(*) FROM spec_series_status"),
            "runs": one("SELECT COUNT(*) FROM crawl_run"),
            "errors": one("SELECT COUNT(*) FROM crawl_error"),
        }

    def latest_products(self, site_code: str = "", limit: int = 20) -> list[sqlite3.Row]:
        sql = ("SELECT p.id, s.code AS site, p.sku, p.brand, p.model, p.title,"
               " (SELECT price FROM price_snapshot ps WHERE ps.product_id=p.id"
               "  ORDER BY ps.captured_at DESC LIMIT 1) AS last_price,"
               " (SELECT currency FROM price_snapshot ps WHERE ps.product_id=p.id"
               "  ORDER BY ps.captured_at DESC LIMIT 1) AS currency,"
               " (SELECT COUNT(*) FROM review r WHERE r.product_id=p.id) AS n_reviews,"
               " p.last_seen"
               " FROM product p JOIN site s ON s.id=p.site_id")
        args: list = []
        if site_code:
            sql += " WHERE s.code=?"
            args.append(site_code)
        sql += " ORDER BY p.last_seen DESC LIMIT ?"
        args.append(limit)
        return self.conn.execute(sql, args).fetchall()

    def price_history(self, product_id: int, limit: int = 50) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT price, list_price, currency, in_stock, captured_at"
            " FROM price_snapshot WHERE product_id=?"
            " ORDER BY captured_at DESC LIMIT ?", (product_id, limit)).fetchall()

    def products_for_monitor(self, site_code: str, limit: int = 500) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT p.id, p.sku, p.url, p.model, p.title FROM product p"
            " JOIN site s ON s.id=p.site_id WHERE s.code=?"
            " ORDER BY p.last_seen ASC LIMIT ?", (site_code, limit)).fetchall()

    # ---------------- SPEC（日本站规格表）----------------
    def save_spec_sheet(self, sheet: SpecSheet, week: str,
                        replace_week: bool = False) -> int:
        """把一个系列的 SPEC 表按周快照写入，返回 series_id。

        ``replace_week=True`` 用于完整成功的本周重抓：在同一事务中删除该系列同周
        旧行，再写入本次完整解析结果。部分型号失败时使用 ``False``：保留已有的
        系列型号清单和同周型号值，并按型号/项目合并本次成功值，避免不完整结果
        覆盖旧快照。整个删除/写入过程失败时回滚，不能留下半张快照。
        """
        import json as _json
        now = _now()
        try:
            self.conn.execute("BEGIN")
            old_series = self.conn.execute(
                "SELECT id, models FROM spec_series WHERE brand=? AND series=?",
                (sheet.brand, sheet.series),
            ).fetchone()
            old_models: list[str] = []
            if old_series:
                try:
                    loaded = _json.loads(old_series["models"] or "[]")
                    if isinstance(loaded, list):
                        old_models = [str(model) for model in loaded if str(model).strip()]
                except (_json.JSONDecodeError, TypeError):
                    old_models = []
            if replace_week:
                merged_models = list(sheet.models)
            else:
                merged_models = list(old_models)
                for model in sheet.models:
                    if model not in merged_models:
                        merged_models.append(model)

            self.conn.execute(
                "INSERT INTO spec_series(brand, brand_name, series, url, models,"
                " first_seen, last_seen) VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(brand, series) DO UPDATE SET "
                " brand_name=excluded.brand_name,"
                " url=COALESCE(NULLIF(excluded.url,''), spec_series.url),"
                " models=excluded.models, last_seen=excluded.last_seen",
                (sheet.brand, sheet.brand_name, sheet.series, sheet.url,
                 _json.dumps(merged_models, ensure_ascii=False), now, now),
            )
            row = self.conn.execute(
                "SELECT id FROM spec_series WHERE brand=? AND series=?",
                (sheet.brand, sheet.series),
            ).fetchone()
            series_id = int(row["id"])

            if replace_week:
                self.conn.execute(
                    "DELETE FROM spec_row WHERE series_id=? AND captured_week=?",
                    (series_id, week),
                )

            for r in sheet.rows:
                values = {c.model: c.value for c in r.values}
                item_zh = r.item_zh
                row_order = r.order
                if not replace_week:
                    old_row = self.conn.execute(
                        "SELECT item_zh, row_order, values_json FROM spec_row "
                        "WHERE series_id=? AND category=? AND item_ja=? AND captured_week=?",
                        (series_id, r.category, r.item_ja, week),
                    ).fetchone()
                    if old_row:
                        try:
                            old_values = _json.loads(old_row["values_json"] or "{}")
                            if isinstance(old_values, dict):
                                old_values.update(values)
                                values = old_values
                        except (_json.JSONDecodeError, TypeError):
                            pass
                        item_zh = item_zh or old_row["item_zh"] or ""
                        row_order = r.order if r.order is not None else old_row["row_order"]
                self.conn.execute(
                    "INSERT INTO spec_row(series_id, category, item_ja, item_zh,"
                    " row_order, values_json, captured_week, captured_at) "
                    "VALUES (?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(series_id, item_ja, category, captured_week) DO UPDATE SET "
                    " item_zh=excluded.item_zh, values_json=excluded.values_json,"
                    " row_order=excluded.row_order, captured_at=excluded.captured_at",
                    (series_id, r.category, r.item_ja, item_zh, row_order,
                     _json.dumps(values, ensure_ascii=False), week, now),
                )
            self.conn.commit()
            return series_id
        except Exception:
            self.conn.rollback()
            raise

    def log_untranslated(self, item_ja: str, brand: str = "") -> None:
        self.conn.execute(
            "INSERT INTO spec_untranslated(item_ja, brand, seen_count, last_seen) "
            "VALUES (?,?,1,?) "
            "ON CONFLICT(item_ja) DO UPDATE SET seen_count=seen_count+1, last_seen=?",
            (item_ja, brand, _now(), _now()))
        self.conn.commit()

    def spec_series_list(self, brand: str = "") -> list[sqlite3.Row]:
        sql = ("SELECT id, brand, brand_name, series, url, models, last_seen"
               " FROM spec_series")
        args: list = []
        if brand:
            sql += " WHERE brand=?"
            args.append(brand)
        sql += " ORDER BY brand, series"
        return self.conn.execute(sql, args).fetchall()

    def latest_week(self, brand: str) -> str:
        row = self.conn.execute(
            "SELECT MAX(sr.captured_week) w FROM spec_row sr"
            " JOIN spec_series ss ON ss.id=sr.series_id WHERE ss.brand=?",
            (brand,)).fetchone()
        return row["w"] if row and row["w"] else ""

    def spec_rows_for_series(self, series_id: int, week: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT category, item_ja, item_zh, row_order, values_json"
            " FROM spec_row WHERE series_id=? AND captured_week=?"
            " ORDER BY row_order", (series_id, week)).fetchall()

    def untranslated_list(self, limit: int = 500) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT item_ja, brand, seen_count, last_seen FROM spec_untranslated"
            " ORDER BY seen_count DESC LIMIT ?", (limit,)).fetchall()


    def record_spec_series_status(
        self,
        run_id: int,
        brand: str,
        brand_name: str,
        series: str,
        captured_week: str,
        status: str,
        requested_url: str = "",
        selected_url: str = "",
        attempted_urls: list | None = None,
        series_id: int | None = None,
        model_expected: int = 0,
        model_ok: int = 0,
        model_empty: int = 0,
        model_failed: int = 0,
        row_count: int = 0,
        error_kind: str = "",
        message: str = "",
    ) -> None:
        """记录一个 SPEC 系列本次运行的最终状态，不允许空/失败静默消失。"""
        if status not in {"success", "empty", "failed"}:
            raise ValueError(f"非法 SPEC 系列状态: {status}")
        now = _now()
        self.conn.execute(
            "INSERT INTO spec_series_status("
            "run_id, brand, brand_name, series, captured_week, status, status_source, "
            "requested_url, selected_url, attempted_urls, series_id, model_expected, "
            "model_ok, model_empty, model_failed, row_count, error_kind, message, "
            "started_at, finished_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(run_id, brand, series) DO UPDATE SET "
            "brand_name=excluded.brand_name, captured_week=excluded.captured_week, "
            "status=excluded.status, status_source=excluded.status_source, "
            "requested_url=excluded.requested_url, selected_url=excluded.selected_url, "
            "attempted_urls=excluded.attempted_urls, series_id=excluded.series_id, "
            "model_expected=excluded.model_expected, model_ok=excluded.model_ok, "
            "model_empty=excluded.model_empty, model_failed=excluded.model_failed, "
            "row_count=excluded.row_count, error_kind=excluded.error_kind, "
            "message=excluded.message, started_at=excluded.started_at, "
            "finished_at=excluded.finished_at",
            (
                run_id,
                brand,
                brand_name,
                series,
                captured_week,
                status,
                "crawl",
                requested_url[:500],
                selected_url[:500],
                json.dumps(attempted_urls or [], ensure_ascii=False),
                series_id,
                model_expected,
                model_ok,
                model_empty,
                model_failed,
                row_count,
                error_kind[:100],
                message[:2000],
                now,
                now,
            ),
        )
        self.conn.commit()

    def record_spec_entry_audit(
        self,
        run_id: int,
        brand: str,
        brand_name: str,
        requested_url: str = "",
        expected_series_count: int | None = None,
        discovered_series_count: int = 0,
        expected_model_count: int | None = None,
        discovered_model_count: int = 0,
        termination_reason: str = "",
        details: dict | None = None,
    ) -> None:
        """保存入口发现数量、期望值和稳定/分页终止原因。"""
        self.conn.execute(
            "INSERT INTO spec_entry_audit("
            "run_id, brand, brand_name, requested_url, expected_series_count, "
            "discovered_series_count, expected_model_count, discovered_model_count, "
            "termination_reason, details_json, captured_at) VALUES (?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(run_id, brand) DO UPDATE SET "
            "brand_name=excluded.brand_name, requested_url=excluded.requested_url, "
            "expected_series_count=excluded.expected_series_count, "
            "discovered_series_count=excluded.discovered_series_count, "
            "expected_model_count=excluded.expected_model_count, "
            "discovered_model_count=excluded.discovered_model_count, "
            "termination_reason=excluded.termination_reason, "
            "details_json=excluded.details_json, captured_at=excluded.captured_at",
            (
                run_id, brand, brand_name, str(requested_url or "")[:500],
                expected_series_count, int(discovered_series_count or 0),
                expected_model_count, int(discovered_model_count or 0),
                str(termination_reason or "")[:120],
                json.dumps(details or {}, ensure_ascii=False), _now(),
            ),
        )
        self.conn.commit()

    def latest_spec_entry_audit(self, brand: str = "") -> sqlite3.Row | None:
        """返回某品牌最近一次入口发现审计；不存在时返回 None。"""
        sql = "SELECT * FROM spec_entry_audit"
        args: list = []
        if brand:
            sql += " WHERE brand=?"
            args.append(brand)
        sql += " ORDER BY captured_at DESC, id DESC LIMIT 1"
        try:
            return self.conn.execute(sql, args).fetchone()
        except sqlite3.OperationalError:
            # 兼容未执行 v7 schema 的旧库。
            return None

    def spec_entry_audit_list(self, brand: str = "", limit: int = 100) -> list[sqlite3.Row]:
        sql = "SELECT * FROM spec_entry_audit"
        args: list = []
        if brand:
            sql += " WHERE brand=?"
            args.append(brand)
        sql += " ORDER BY captured_at DESC, id DESC LIMIT ?"
        args.append(max(1, int(limit)))
        try:
            return self.conn.execute(sql, args).fetchall()
        except sqlite3.OperationalError:
            return []

    def spec_series_status_list(
        self,
        brand: str = "",
        captured_week: str = "",
        status: str = "",
    ) -> list[sqlite3.Row]:
        """查询系列状态明细；不依赖是否存在成功的 spec_series。"""
        sql = "SELECT * FROM spec_series_status WHERE 1=1"
        args: list[str] = []
        if brand:
            sql += " AND brand=?"
            args.append(brand)
        if captured_week:
            sql += " AND captured_week=?"
            args.append(captured_week)
        if status:
            sql += " AND status=?"
            args.append(status)
        sql += " ORDER BY finished_at DESC, id DESC"
        return self.conn.execute(sql, args).fetchall()
    # ================= 店铺维度价格（kakaku 等多店铺报价站点）=================

    def add_shop_prices(self, product_id: int, shop_prices: dict[str, float | int],
                        currency: str = "", lowest_shop: str = "") -> int:
        """写入一次采集中各店铺的报价（★时序，只 INSERT）。返回写入条数。

        shop_prices: {店铺名: 价格}；lowest_shop 命中的店铺标记 is_lowest=1。
        价格为空/非数值的店铺跳过（该店本商品无在售）。
        """
        rows = []
        now = _now()
        for shop, price in (shop_prices or {}).items():
            if price is None or price == "":
                continue
            try:
                value = float(price)
            except (TypeError, ValueError):
                continue
            rows.append((product_id, shop, value, currency,
                         1 if (lowest_shop and shop == lowest_shop) else 0, now))
        if not rows:
            return 0
        self.conn.executemany(
            "INSERT INTO price_shop_snapshot(product_id, shop, price, currency,"
            " is_lowest, captured_at) VALUES (?,?,?,?,?,?)", rows)
        self.conn.commit()
        return len(rows)

    def shop_price_history(self, product_id: int, shop: str = "",
                           limit: int = 200) -> list[sqlite3.Row]:
        """某商品的店铺价格历史；shop 非空时只看该店铺。"""
        sql = ("SELECT shop, price, currency, is_lowest, captured_at"
               " FROM price_shop_snapshot WHERE product_id=?")
        args: list = [product_id]
        if shop:
            sql += " AND shop=?"
            args.append(shop)
        sql += " ORDER BY captured_at DESC, id DESC LIMIT ?"
        args.append(limit)
        return self.conn.execute(sql, args).fetchall()

    def latest_shop_prices(self, product_id: int) -> list[sqlite3.Row]:
        """某商品最近一次采集的各店铺报价（按 captured_at 取最新那批）。"""
        row = self.conn.execute(
            "SELECT MAX(captured_at) AS t FROM price_shop_snapshot WHERE product_id=?",
            (product_id,)).fetchone()
        if not row or not row["t"]:
            return []
        return self.conn.execute(
            "SELECT shop, price, currency, is_lowest, captured_at"
            " FROM price_shop_snapshot WHERE product_id=? AND captured_at=?"
            " ORDER BY price", (product_id, row["t"])).fetchall()

    # ================= 周度价格趋势（价格曲线数据源） =================

    def weekly_price_series(self, site_code: str = "kakaku_jp",
                            weeks_back: int = 0) -> list[sqlite3.Row]:
        """周度价格时序：每个产品 × 每个渠道 × 每周一条，取该周内最后一次报价。

        返回行：product_id, model, brand, size, shop, price, currency,
               is_lowest, week(YYYY-Www), captured_at。

        周键用 strftime('%Y-W%W', captured_at)（与 weekly_kakaku_price 一致）。
        同一周内多次采集时取 MAX(captured_at) 那条，避免噪声。
        weeks_back 限制最近 N 周（<=0 表示不限）。
        """
        where = "WHERE s.code = ?"
        args: list = [site_code]
        if weeks_back and weeks_back > 0:
            where += (" AND ps.captured_at >= datetime("
                      "(SELECT MAX(captured_at) FROM price_shop_snapshot), ?)")
            args.append(f"-{weeks_back * 7} days")
        sql = (
            "WITH ranked AS ("
            "  SELECT p.id AS product_id, p.model, p.brand, p.size, ps.shop, "
            "         ps.price, ps.currency, ps.is_lowest, ps.captured_at, "
            "         strftime('%Y-W%W', ps.captured_at) AS week, "
            "         ROW_NUMBER() OVER ("
            "           PARTITION BY p.id, ps.shop, "
            "                        strftime('%Y-W%W', ps.captured_at)"
            "           ORDER BY ps.captured_at DESC, ps.id DESC) AS rn "
            "  FROM price_shop_snapshot ps "
            "  JOIN product p ON p.id = ps.product_id "
            "  JOIN site s ON s.id = p.site_id "
            f"  {where}) "
            "SELECT product_id, model, brand, size, shop, price, currency, "
            "       is_lowest, week, captured_at FROM ranked WHERE rn = 1 "
            "ORDER BY brand, model, shop, week")
        return self.conn.execute(sql, args).fetchall()

    # ================= kakaku 型号级任务状态 =================

    _KAKAKU_STATUSES = {
        "ok", "no_item", "not_target_year", "no_reviews", "no_price",
        "no_release", "blocked", "failed",
        # 7.1 目标年份预筛：命中缓存且明确非目标年份而跳过（不访问站点）。
        "skipped_by_release_cache",
        # 7.6 已知无对应商品清单：跳过历史确认过 no_item 的型号。
        "skipped_known_no_item",
    }

    def release_year_lookup(self, brand: str = "", task: str = "review",
                            ) -> dict[str, dict]:
        """读取已入库的「型号 → 发售年份/item_id/时间」缓存，供 7.1 预筛。

        返回 {model_upper: {"release_year", "item_id", "captured_at", "status"}}，
        只取每个型号最近一条 kakaku_task_status。model 以大写归一，便于与
        型号清单比较。缺表/无数据时返回空 dict，不抛异常。
        """
        sql = ("SELECT model, item_id, release_year, status, captured_at"
               " FROM kakaku_task_status WHERE release_year IS NOT NULL")
        args: list = []
        if task:
            sql += " AND task=?"
            args.append(task)
        if brand:
            sql += " AND brand=?"
            args.append(brand)
        sql += " ORDER BY captured_at ASC, id ASC"
        out: dict[str, dict] = {}
        try:
            for r in self.conn.execute(sql, args):
                model = str(r["model"] or "").strip()
                if not model:
                    continue
                out[model.upper()] = {
                    "release_year": r["release_year"],
                    "item_id": r["item_id"] or "",
                    "status": r["status"] or "",
                    "captured_at": r["captured_at"] or "",
                }
        except sqlite3.Error:
            return {}
        return out

    def known_no_item_models(self, brand: str = "", task: str = "review",
                             ) -> set[str]:
        """返回历史被判为 no_item 的型号（大写归一），供 7.6 跳过。"""
        sql = "SELECT DISTINCT model FROM kakaku_task_status WHERE status='no_item'"
        args: list = []
        if task:
            sql += " AND task=?"
            args.append(task)
        if brand:
            sql += " AND brand=?"
            args.append(brand)
        try:
            return {str(r["model"]).strip().upper()
                    for r in self.conn.execute(sql, args) if r["model"]}
        except sqlite3.Error:
            return set()

    # ---------------- 7.2 讨论主题增量状态 ----------------
    def thread_states(self, item_id: str) -> dict[str, sqlite3.Row]:
        """取某商品所有已记录的讨论主题状态，键为 thread_url。缺表返回空。"""
        try:
            rows = self.conn.execute(
                "SELECT * FROM kakaku_thread_state WHERE item_id=?", (item_id,)
            ).fetchall()
        except sqlite3.Error:
            return {}
        return {r["thread_url"]: r for r in rows}

    def upsert_thread_state(self, item_id: str, thread_url: str,
                            thread_title: str = "", last_post_key: str = "",
                            post_count: int = 0) -> None:
        """写入/更新讨论主题的最后帖号与帖数，供下次增量比较。"""
        try:
            self.conn.execute(
                "INSERT INTO kakaku_thread_state(item_id, thread_url, thread_title,"
                " last_post_key, post_count, last_seen) VALUES (?,?,?,?,?,?)"
                " ON CONFLICT(item_id, thread_url) DO UPDATE SET"
                "  thread_title=excluded.thread_title,"
                "  last_post_key=excluded.last_post_key,"
                "  post_count=excluded.post_count, last_seen=excluded.last_seen",
                (item_id, thread_url, thread_title, last_post_key,
                 int(post_count or 0), _now()))
            self.conn.commit()
        except sqlite3.Error:
            pass

    def record_kakaku_status(
        self,
        task: str,
        brand: str,
        model: str,
        status: str,
        run_id: int | None = None,
        brand_name: str = "",
        item_id: str = "",
        product_id: int | None = None,
        release_text: str = "",
        release_year: int | None = None,
        size: str = "",
        row_count: int = 0,
        new_count: int = 0,
        message: str = "",
    ) -> None:
        """记录 kakaku 价格/评论/发售日抓取的型号级结果，空与失败都不静默。

        与 spec_series_status 同一思路：状态先落库，前端才能展示缺口原因。
        (run_id, task, brand, model) 唯一，重复写入按 upsert 覆盖同一次运行的结果。
        """
        if task not in {"price", "review", "release"}:
            raise ValueError(f"非法 kakaku 任务类型: {task}")
        if status not in self._KAKAKU_STATUSES:
            raise ValueError(f"非法 kakaku 抓取状态: {status}")
        self.conn.execute(
            "INSERT INTO kakaku_task_status(run_id, task, brand, brand_name, model,"
            " item_id, product_id, status, release_text, release_year, size,"
            " row_count, new_count, message, captured_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(run_id, task, brand, model) DO UPDATE SET"
            "  brand_name=excluded.brand_name, item_id=excluded.item_id,"
            "  product_id=excluded.product_id, status=excluded.status,"
            "  release_text=excluded.release_text, release_year=excluded.release_year,"
            "  size=excluded.size, row_count=excluded.row_count,"
            "  new_count=excluded.new_count, message=excluded.message,"
            "  captured_at=excluded.captured_at",
            (run_id, task, brand, brand_name or brand, model, item_id, product_id,
             status, release_text, release_year, size, row_count, new_count,
             message, _now()))
        self.conn.commit()

    def kakaku_status_list(self, task: str = "", brand: str = "",
                           status: str = "", limit: int = 1000) -> list[sqlite3.Row]:
        """查询 kakaku 型号级状态明细（前端「哪些没抓到、为什么」用这个）。"""
        sql = "SELECT * FROM kakaku_task_status WHERE 1=1"
        args: list = []
        if task:
            sql += " AND task=?"
            args.append(task)
        if brand:
            sql += " AND brand=?"
            args.append(brand)
        if status:
            sql += " AND status=?"
            args.append(status)
        sql += " ORDER BY captured_at DESC, id DESC LIMIT ?"
        args.append(limit)
        return self.conn.execute(sql, args).fetchall()

    def kakaku_status_counts(self, task: str = "", brand: str = "") -> dict[str, int]:
        """按每个日本型号最新状态聚合，避免历史运行累计污染概览。"""
        sql = "SELECT k.status, COUNT(*) AS n FROM kakaku_task_status k WHERE 1=1"
        args: list = []
        if task:
            sql += " AND k.task=?"
            args.append(task)
        if brand:
            sql += " AND k.brand=?"
            args.append(brand)
        sql += (" AND NOT EXISTS (SELECT 1 FROM kakaku_task_status newer "
                "WHERE newer.task=k.task AND newer.brand=k.brand "
                "AND newer.model=k.model AND newer.id>k.id) "
                "GROUP BY k.status ORDER BY n DESC")
        try:
            return {row["status"]: int(row["n"])
                    for row in self.conn.execute(sql, args)}
        except sqlite3.Error:
            return {}

    def retail_status_counts(self, region: str = "", task: str = "",
                             site_code: str = "", brand: str = "") -> dict[str, int]:
        """按每个地区/任务/站点/品牌/型号最新状态聚合。"""
        sql = "SELECT r.status, COUNT(*) AS n FROM retail_task_status r WHERE 1=1"
        args: list = []
        if region:
            sql += " AND r.region=?"
            args.append(region)
        if task:
            sql += " AND r.task=?"
            args.append(task)
        if site_code:
            sql += " AND r.site_code=?"
            args.append(site_code)
        if brand:
            sql += " AND r.brand=?"
            args.append(brand)
        sql += (" AND NOT EXISTS (SELECT 1 FROM retail_task_status newer "
                "WHERE newer.region=r.region AND newer.task=r.task "
                "AND newer.site_code=r.site_code AND newer.brand=r.brand "
                "AND newer.model=r.model AND newer.id>r.id) "
                "GROUP BY r.status ORDER BY n DESC")
        try:
            return {r["status"]: int(r["n"])
                    for r in self.conn.execute(sql, args)}
        except sqlite3.Error:
            return {}

    def retail_status_list(self, region: str = "", task: str = "",
                           site_code: str = "", brand: str = "",
                           model: str = "", status: str = "",
                           limit: int = 1000) -> list[sqlite3.Row]:
        """查询通用零售型号×站点状态明细。"""
        sql = "SELECT * FROM retail_task_status WHERE 1=1"
        args: list = []
        if region:
            sql += " AND region=?"
            args.append(region)
        if task:
            sql += " AND task=?"
            args.append(task)
        if site_code:
            sql += " AND site_code=?"
            args.append(site_code)
        if brand:
            sql += " AND brand=?"
            args.append(brand)
        if model:
            sql += " AND UPPER(model)=UPPER(?)"
            args.append(model)
        if status:
            sql += " AND status=?"
            args.append(status)
        sql += " ORDER BY captured_at DESC, id DESC LIMIT ?"
        args.append(limit)
        try:
            return self.conn.execute(sql, args).fetchall()
        except sqlite3.Error:
            return []

    def record_retail_status(
        self,
        region: str,
        task: str,
        site_code: str,
        brand: str,
        model: str,
        status: str,
        run_id: int | None = None,
        site_name: str = "",
        brand_name: str = "",
        item_id: str = "",
        product_id: int | None = None,
        size: str = "",
        price: float | None = None,
        currency: str = "",
        row_count: int = 0,
        new_count: int = 0,
        message: str = "",
    ) -> None:
        """记录一个零售站型号的价格或评价结果，不污染日本 kakaku 状态。"""
        if task not in {"price", "review"}:
            raise ValueError(f"非法零售任务类型: {task}")
        if run_id is None:
            raise ValueError("retail_task_status 必须绑定 run_id，不能写入 NULL 状态")
        if status not in {"ok", "no_item", "no_price", "no_reviews",
                          "summary_only", "blocked", "failed"}:
            raise ValueError(f"非法零售抓取状态: {status}")
        self.conn.execute(
            "INSERT INTO retail_task_status("
            "run_id, region, task, site_code, site_name, brand, brand_name, model, "
            "item_id, product_id, status, size, price, currency, row_count, new_count, "
            "message, captured_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(run_id, task, region, site_code, brand, model) DO UPDATE SET "
            "site_name=excluded.site_name, brand_name=excluded.brand_name, "
            "item_id=excluded.item_id, product_id=excluded.product_id, "
            "status=excluded.status, size=excluded.size, price=excluded.price, "
            "currency=excluded.currency, row_count=excluded.row_count, "
            "new_count=excluded.new_count, message=excluded.message, "
            "captured_at=excluded.captured_at",
            (run_id, region, task, site_code, site_name, brand, brand_name, model,
             item_id, product_id, status, size, price, currency, row_count, new_count,
             message[:2000], _now()),
        )
        self.conn.commit()

    # ================= 后端查询辅助（按型号定位商品）=================

    def find_product(self, site_code: str, sku: str) -> sqlite3.Row | None:
        """按站点 code + sku（kakaku 为 item_id）取商品行。"""
        return self.conn.execute(
            "SELECT p.* FROM product p JOIN site s ON s.id=p.site_id"
            " WHERE s.code=? AND p.sku=?", (site_code, sku)).fetchone()

    def products_by_model(self, model: str, site_code: str = "") -> list[sqlite3.Row]:
        """按型号找商品（跨站点比价用）。model 做大小写不敏感匹配。"""
        sql = ("SELECT p.*, s.code AS site_code FROM product p"
               " JOIN site s ON s.id=p.site_id WHERE UPPER(p.model)=UPPER(?)")
        args: list = [model]
        if site_code:
            sql += " AND s.code=?"
            args.append(site_code)
        sql += " ORDER BY s.code"
        return self.conn.execute(sql, args).fetchall()

    def reviews_for_product(self, product_id: int, limit: int = 200,
                            offset: int = 0) -> list[sqlite3.Row]:
        """评论分页（前端评论列表用）。"""
        return self.conn.execute(
            "SELECT * FROM review WHERE product_id=?"
            " ORDER BY review_date DESC, id DESC LIMIT ? OFFSET ?",
            (product_id, limit, offset)).fetchall()
