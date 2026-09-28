"""领域数据模型。

站点适配器统一产出这些结构，存储层与场景层都不关心数据来自哪个站点，
这样新增站点不会影响下游（沿用 cert_verify 里 CertRecord 的思路）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

# 站点防护等级：决定用哪种 Fetcher 和多大的抓取间隔
L1_MILD = "L1"        # 温和：纯 HTTP 即可
L2_MEDIUM = "L2"      # 中等：需要真实浏览器
L3_STRONG = "L3"      # 强防护：浏览器 + 低频 + 可能需代理/人工介入


@dataclass
class Product:
    """商品主体。以 (site_code, sku) 唯一标识。"""
    site_code: str
    sku: str                      # ASIN / 站内商品号
    url: str = ""
    title: str = ""
    brand: str = ""
    model: str = ""               # 厂商型号，跨站比价的关键
    category: str = ""
    size: str = ""                # 尺寸（如 65 Inch），对应导出表「尺寸」列
    # 商品页自报的权威型号码（可多个）。Costco 这类站的标题写「55 Class - U7SG
    # Series」而不是「55U7SG」，靠标题子串匹配既漏又易错；把 PDP 自己标注的
    # Model 字段（页头 item-number、规格表 Model 行）收进来做**精确相等**校验，
    # 是防张冠李戴最可靠的一路。非空时校验以它为准。
    model_candidates: list[str] = field(default_factory=list)
    # BazaarVoice 评价接口用的商品号（Costco 的 data-bv-product-id，通常等于 URL
    # 里 `.product.<id>.html` 的 id）。落到独立字段，评价线据此拉明细。
    bv_product_id: str = ""


@dataclass
class SpecItem:
    """规格键值。用 KV 而非宽表：不同品类字段差异过大。"""
    key: str
    value: str


@dataclass
class PriceSnapshot:
    """价格快照（时序，只增不改）。"""
    price: float | None = None
    list_price: float | None = None
    currency: str = ""
    in_stock: bool | None = None
    raw_text: str = ""


@dataclass
class Review:
    """单条评价。review_key 为站内唯一 id，用于增量去重。

    图片/视频字段对应导出表的「图片数量/图片地址/是否有视频/视频地址」。
    image_urls / video_urls 存 URL 列表（导出时用分隔符连接）。
    """
    review_key: str
    rating: float | None = None
    title: str = ""
    body: str = ""
    author: str = ""
    review_date: str = ""
    verified: bool | None = None
    helpful_count: int | None = None
    review_url: str = ""                         # 评论链接
    image_urls: list[str] = field(default_factory=list)
    video_urls: list[str] = field(default_factory=list)

    @property
    def image_count(self) -> int:
        return len(self.image_urls)

    @property
    def has_video(self) -> bool:
        return bool(self.video_urls)


@dataclass
class ReviewSummary:
    """评分聚合快照（时序，只增不改）。"""
    avg_rating: float | None = None
    total_count: int | None = None
    stars: dict[int, int] = field(default_factory=dict)   # {5: 120, 4: 30, ...}
    # 平台生成的评价摘要原文。Amazon 的「Customers say」实测可稳定抽取
    # （选择器 [data-testid='overall-summary']），保持原文不做二次加工。
    summary_text: str = ""
    # 评分直方图 {星级: 占比百分比}。Amazon 的直方图在 #histogramTable 内，
    # 但需要 hover 才渲染出各行，故默认留空、由能触发的站点填充。
    rating_percent: dict[str, float] = field(default_factory=dict)


@dataclass
class RankingItem:
    """榜单排名快照（时序）。"""
    list_name: str
    rank: int | None = None


@dataclass
class ProductPayload:
    """一次商品页采集的完整产出，交给存储层落库。"""
    product: Product
    specs: list[SpecItem] = field(default_factory=list)
    price: PriceSnapshot | None = None
    reviews: list[Review] = field(default_factory=list)
    summary: ReviewSummary | None = None
    ranking: RankingItem | None = None

    def stat(self) -> str:
        return (f"{self.product.sku} "
                f"specs={len(self.specs)} "
                f"price={'Y' if self.price and self.price.price is not None else 'N'} "
                f"reviews={len(self.reviews)}")


@dataclass
class SearchHit:
    """搜索结果条目（列表页产出，随后进详情页）。"""
    sku: str
    url: str = ""
    title: str = ""
    rank: int | None = None


# ==================== SPEC 采集（日本站规格表）====================


@dataclass
class SpecCell:
    """一行 SPEC 里某个机型的取值。"""
    model: str                    # 机型号，如 85U8S
    value: str = ""


@dataclass
class SpecRow:
    """一行 SPEC 项目。

    对齐目标表格：区分 | 项目 | 项目(中文) | 机型1 | 机型2 | ...
    values 里每个机型一个 SpecCell；若该项目所有机型同值，也逐机型展开便于导出。
    """
    category: str = ""            # 区分（分组），如 基本仕様 / 高画質
    item_ja: str = ""             # 项目（日文），如 型番 / パネル
    item_zh: str = ""             # 项目（中文），查词典填入
    values: list[SpecCell] = field(default_factory=list)
    order: int = 0                # 行序，保证导出顺序与官网一致

    def value_for(self, model: str) -> str:
        for c in self.values:
            if c.model == model:
                return c.value
        # 全机型同值时可能只有一个无差异 cell
        if len(self.values) == 1 and self.values[0].model in ("", "*"):
            return self.values[0].value
        return ""


@dataclass
class SpecSheet:
    """一个系列的完整 SPEC 表。

    以 (brand, series) 唯一。models 是该系列的机型列表（列顺序），rows 是 SPEC 行。
    """
    brand: str                    # 品牌 code，如 hisense_jp
    brand_name: str               # 展示名，如 Hisense（日本）
    series: str                   # 系列，如 U8S
    url: str = ""
    models: list[str] = field(default_factory=list)
    rows: list[SpecRow] = field(default_factory=list)

    def stat(self) -> str:
        return f"{self.brand}/{self.series} models={len(self.models)} rows={len(self.rows)}"
