"""Best Buy 美国站适配器（价格 + 网评同页；★必须美国出口 IP）。

防护等级 L3（强）。**关于"连不上"的真正原因（2026-09-20 复核）**：
本机所在网络是**公司内网准入网关白名单**拦的（`http://10.18.0.250/disable/disable.htm`
?url_type=访问网站/工业设计白名单），HTTPS 握手还被企业根证书中间人拦
（`SEC_E_UNTRUSTED_ROOT`），出口 IP 是中国（27.223.99.190）。
**这既不是 Best Buy 反爬，也不是简单的"需要代理"——调试站点反爬策略前必须先区分这两者。**

即使网络通了，非美国 IP 的返回也**不是 403**：Best Buy 会返回
**HTTP 200** + `<title>Best Buy International: Select your Country - Best Buy</title>`。
所以判断"是否拿到美国站"必须看**标题/正文**，不能看状态码。

URL 形态（外部实测证据，urlscan 存档 2025-12，**本地待现场确认**）：
- 搜索页：`/site/searchpage.jsp?st=<kw>&intl=nosplash`
  · `intl=nosplash` 用于抑制国际选择页插屏（实测带该参数返回正常搜索页）；
  · 翻页用 `cp=N`（实测 `?cp=2` / `?cp=3` / `?cp=100`），每页 18 条，页码上限 100；
  · 旧串还有 `id=pcat17071`（搜索类目 id），必要时作为兜底加上。
- 商品页两种形态，**旧形态仍可用**：
  · 旧：`/site/<slug>/<skuId>.p?skuId=<skuId>`（本适配器只用 skuId 直达）
  · 新：`/product/<slug>/<10 位码>/sku/<skuId>`
- **型号是服务端渲染的**：PDP HTML 里有 `Model:75Q651G` 与 `SKU:6579448`，
  且型号会拼进 `<title>`（"... 75Q651G - Best Buy"）。
  → 这是"防张冠李戴"闸三（权威型号精确校验）能成立的关键前提。

网评：外部证据显示走**站内原生评价页** `/site/reviews/<slug>/<skuId>`
（20 条/页，页面写 "Page 1 Showing 1-20 of 47"）。**没有 BazaarVoice 证据**：
urlscan 里 `domain:api.bazaarvoice.com AND page.domain:www.bestbuy.com` 为 0 命中，
页面出现 bazaarvoice 域名的最后记录在 2022-12。
→ 因此不要假设走 `overseas/bv_reviews.py`；先跑 `scripts/diag_bestbuy_us.py --only reviews` 定类。
"""
from __future__ import annotations

import urllib.parse

from ...models import L3_STRONG
from .. import SiteAdapter


class BestBuyUsAdapter(SiteAdapter):
    code = "bestbuy_us"
    name = "Best Buy US"
    base_url = "https://www.bestbuy.com"
    country = "USA"
    channel = "Best Buy"
    protection = L3_STRONG
    requires_browser = True
    suggested_interval = 10.0
    # ★ 2026-09-20 实测：**正常搜索页**（1.86 MB 真实结果）的 `<head>` 里有
    # `<script src="https://www.gstatic.com/recaptcha/releases/.../recaptcha__en.js">`
    # （表单用的 Google reCAPTCHA），会被 BlockDetector 的 `captcha` 关键字判成
    # "被拦截"——实测导致该站被误报为 blocked。
    # 豁免之所以安全：本页的两种**真实**拦截各有多带一条更具体的标记——
    #   国际选择页 → `best buy international`
    #   Akamai 拒绝页 → `pardon our interruption`
    # 两条都仍在 `_BLOCK_MARKERS` 里，且不会被 `captcha` 豁免影响。
    # ★ 遗留：本适配器目前**没有** `supports_retail`，所以它会被
    #   `crawl_ca_retail.crawl_site` 当"非零售站"跳过（`[SKIP]`，且不写状态）。
    #   本站在 regions_config 里 enabled=False，故当前无影响；但若将来启用，
    #   必须同时补 `supports_retail = True` 与 `parse_product`，否则会**静默不抓**。
    block_marker_allowlist = ("captcha",)

    def search_url(self, keyword: str, page: int = 1) -> str:
        """搜索页。带 intl=nosplash 抑制国际选择页插屏；翻页参数是 cp。

        ★ 非美国 IP 下这个 URL 会返回 HTTP 200 的"Best Buy International"页，
        状态码正常但内容不是搜索结果——必须靠标题/正文识别，见 `is_country_splash`。
        """
        q = urllib.parse.urlencode({"st": keyword, "intl": "nosplash"})
        url = f"{self.base_url}/site/searchpage.jsp?{q}"
        if page and page > 1:
            url += f"&cp={int(page)}"
        return url

    def product_url(self, sku: str) -> str:
        # sku 为 skuId（纯数字）；商品页需 slug，但 skuId 直达通常会重定向到正确 PDP。
        # 新形态 /product/<slug>/<code>/sku/<skuId> 存在，但需要 slug，故这里仍走旧形态。
        return f"{self.base_url}/site/{sku}.p?skuId={sku}"

    def reviews_url(self, sku: str, page: int = 1) -> str:
        """站内原生评价页（**兜底路径，不是首选**）。

        ★三条实测约束（外部证据，urlscan 捕获，本地待确认）：
        1. 所有确认可用的评价**展示** URL 都带 slug：`/site/reviews/<slug>/<skuId>`；
           **slug-less 的 `/site/reviews/<skuId>` 有没有效，无任何证据**（只证明了
           写路径 `/site/reviews/submission_2/<skuId>` 容忍缺 slug）。
           → 首选应是 `/ugc/v2/reviews?page=1&pageSize=20&sku=<SKU>&sort=MOST_RECENT`
             （一手 JSON 接口，按设计只要 SKU，且不需要渲染 JS）；
             要拼这个 HTML 页时，用 PDP 自己的 canonical slug，别用 sku-only 形态。
        2. 翻页参数名：中等把握是 `page`（1-based）+ `pageSize`；`?rating=1-5` 已实测。
           **★不要复用 `cp`**——那是**搜索页**的翻页参数，评价页无此参数证据。
        3. 评价**不需要登录**（实测游客态就能看到完整评价正文、用户名、Verified
           Purchase 徽章、星级直方图和 AI 摘要）。US IP 仍是必需的（Akamai +
           reCAPTCHA），且不能只靠 IP：要同时带 `intl=nosplash` 并按标题识别国际选择页。
        """
        url = f"{self.base_url}/site/reviews/{sku}"
        if page and page > 1:
            url += f"?page={int(page)}"
        return url

    @staticmethod
    def is_country_splash(title: str = "", html: str = "") -> bool:
        """是否拿到了"国际选择页"而不是美国站内容。

        ★必须用这个而不是状态码：非美国 IP 是 HTTP 200 + 国际选择页标题。
        """
        import re
        text = f"{title or ''}\n{(html or '')[:6000]}"
        return bool(re.search(
            r"Best Buy International|Select your Country", text, re.I))
