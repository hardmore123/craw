"""区域（线）→ 品牌 / SPEC 官网 / 零售站 的集中配置。

背景：日本线把「品牌清单」和「品牌=站点」的假设硬编码在 5 处
（crawl_kakaku_prices.py、crawl_kakaku_reviews.py、refresh_spec.py、
export_spec_models.py、api/regions.py）。新增国家线若继续复制这套硬编码，
技术债会随线数量线性膨胀。

本模块把「一条线由哪些品牌、SPEC 从哪些官网站点抓、价格/网评从哪些零售站抓」
统一声明为数据，供上述脚本和 API 按 region 读取，站点适配器仍按目录自动发现。

关键区别（相对日本线）：
- 日本线：SPEC 站点 code == 品牌 code（hisense_jp 既是品牌又是官网站点），
  价格/网评统一走单一聚合站 kakaku_jp。
- 加拿大线：每个品牌一个官网 SPEC 站点（hisense_ca 等），价格/网评分散在
  多个零售站（amazon_ca / bestbuy_ca / ...），因此「品牌」与「零售站」是
  多对多：同一型号要在每个零售站各搜一次。

数据结构（纯数据，不 import 适配器，避免循环依赖）：
    Region(code, name, currency, locale, enabled,
           brands=[Brand(...)],            # 该线的品牌
           spec_sites=[SpecSite(...)],     # 该线的 SPEC 官网站点（每品牌一个）
           retail_sites=[RetailSite(...)]) # 该线的价格/网评零售站
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Brand:
    """一条线里的一个品牌。"""
    code: str                 # 品牌 code，如 hisense_ca；SPEC 站点 code 与之一致
    name: str                 # 展示名，如 Hisense
    search_name: str = ""     # 在零售站搜索时用的品牌名（默认取 name）

    @property
    def query_name(self) -> str:
        return self.search_name or self.name


@dataclass(frozen=True)
class SpecSite:
    """一条线里的一个 SPEC 官网站点（对应 sites/<code>/adapter.py）。"""
    site: str                 # 适配器 code，如 hisense_ca
    brand: str                # 归属品牌 code
    name: str                 # 展示名
    probe_url: str = ""       # 通道验证 URL


@dataclass(frozen=True)
class RetailSite:
    """一条线里的一个零售站（价格 + 网评来源，同一商品页同步抓取）。"""
    site: str                 # 适配器 code，如 amazon_ca
    name: str                 # 展示名，如 Amazon
    probe_url: str = ""       # 通道验证 URL
    enabled: bool = True      # 该零售站是否纳入抓取（强防护站可先关）


@dataclass(frozen=True)
class Region:
    """一条国家/地区线。"""
    code: str                 # jp / ca / us / mx / eu / sa
    name: str                 # 展示名
    currency: str = ""        # 价格币种，如 CAD
    locale: str = ""          # 站点语言，如 en-CA
    enabled: bool = False     # 前端是否可选、通道验证是否纳入
    note: str = ""
    brands: list[Brand] = field(default_factory=list)
    spec_sites: list[SpecSite] = field(default_factory=list)
    retail_sites: list[RetailSite] = field(default_factory=list)

    def brand_codes(self) -> list[str]:
        return [b.code for b in self.brands]

    def spec_site_codes(self) -> list[str]:
        return [s.site for s in self.spec_sites]

    def retail_site_codes(self, only_enabled: bool = True) -> list[str]:
        return [r.site for r in self.retail_sites if (r.enabled or not only_enabled)]


# ==================== 加拿大线（北美洲 / AM）====================
# TV 电视线。SPEC 从 6 个品牌官网抓；价格 + 网评从 8 个零售站同一商品页同步抓。
# 站点适配器分批实现，retail_site.enabled 控制是否纳入抓取（强防护站先关）。

_CA = Region(
    code="ca",
    name="加拿大",
    currency="CAD",
    locale="en-CA",
    enabled=True,    # 6 品牌官网 SPEC + amazon_ca 零售已就绪；其余零售站验证后逐个开
    note="北美洲加拿大 TV 线：SPEC 走 6 品牌官网，价格+网评走多个零售站",
    brands=[
        Brand("hisense_ca", "Hisense"),
        Brand("samsung_ca", "Samsung"),
        Brand("tcl_ca", "TCL"),
        Brand("lg_ca", "LG"),
        Brand("sony_ca", "Sony"),
        Brand("philips_ca", "Philips"),
    ],
    spec_sites=[
        SpecSite("hisense_ca", "hisense_ca", "Hisense", "https://www.hisense-canada.com/"),
        SpecSite("samsung_ca", "samsung_ca", "Samsung", "https://www.samsung.com/ca/"),
        SpecSite("tcl_ca", "tcl_ca", "TCL", "https://ca-en.tcl.com/"),
        SpecSite("lg_ca", "lg_ca", "LG", "https://www.lg.com/ca_en"),
        SpecSite("sony_ca", "sony_ca", "Sony", "https://www.sony.ca/"),
        SpecSite("philips_ca", "philips_ca", "Philips", "https://www.philips.ca/"),
    ],
    retail_sites=[
        # 强防护站（Amazon/BestBuy/Walmart/Costco/CanadianTire）默认先关，
        # 各自适配器实现 + 低频小样本验证通过后再逐个 enabled=True。
        RetailSite("amazon_ca", "Amazon", "https://www.amazon.ca/", enabled=True),
        RetailSite("bestbuy_ca", "Best Buy", "https://www.bestbuy.ca/en-ca", enabled=False),
        RetailSite("walmart_ca", "Walmart", "https://www.walmart.ca/en", enabled=False),
        RetailSite("costco_ca", "Costco", "https://www.costco.ca/", enabled=False),
        RetailSite("thebrick_ca", "The Brick", "https://www.thebrick.com/", enabled=False),
        RetailSite("leons_ca", "Leon's", "https://www.leons.ca/", enabled=False),
        RetailSite("visions_ca", "Visions", "https://www.visions.ca/", enabled=False),
        RetailSite("canadiantire_ca", "Canadian Tire", "https://www.canadiantire.ca/en.html", enabled=False),
    ],
)


# ==================== 美国线（北美洲 / AM）====================
# TV 电视线。SPEC 从 5 个品牌官网抓；价格 + 网评从 4 个零售站同一商品页同步抓。
# 关键区别（相对日本线，同加拿大线）：SPEC 站点 code == 品牌 code；价格/网评
# 分散在多个零售站，品牌与零售站多对多，同一型号在每个零售站各搜一次。
# 站点适配器分批实现，retail_site.enabled 控制是否纳入抓取（强防护站先关）。

_US = Region(
    code="us",
    name="美国",
    currency="USD",
    locale="en-US",
    enabled=False,   # 5 品牌官网 + 4 零售站适配器逐步接入并小样本验证后再置 True
    note="北美洲美国 TV 线：SPEC 走 5 品牌官网，价格+网评走 4 个零售站同页同步抓",
    brands=[
        Brand("tcl_us", "TCL"),
        Brand("hisense_us", "Hisense"),
        Brand("sony_us", "Sony"),
        Brand("samsung_us", "Samsung"),
        Brand("lg_us", "LG"),
    ],
    spec_sites=[
        SpecSite("tcl_us", "tcl_us", "TCL", "https://us.tcl.com/"),
        SpecSite("hisense_us", "hisense_us", "Hisense", "https://www.hisense-usa.com/"),
        SpecSite("sony_us", "sony_us", "Sony", "https://electronics.sony.com/"),
        SpecSite("samsung_us", "samsung_us", "Samsung", "https://www.samsung.com/us/"),
        SpecSite("lg_us", "lg_us", "LG", "https://www.lg.com/us"),
    ],
    retail_sites=[
        # 强防护站（Amazon/BestBuy/Walmart/Costco）默认先关，各自适配器实现 +
        # 低频小样本验证通过后再逐个 enabled=True。价格与网评在同一商品页同步抓。
        RetailSite("bestbuy_us", "Best Buy", "https://www.bestbuy.com/", enabled=False),
        # walmart_us：非美国 IP 命中 PerimeterX「Robot or human?」验证页，且
        # docs/retail_specs/walmart_us.retail.json 仍是 candidate（选择器未经真实
        # PDP 校准）。按本项目红线「强防护站默认先关，低频小样本验证通过再逐个开」，
        # 这里必须保持 False；校准通过后再改 True。
        RetailSite("walmart_us", "Walmart", "https://www.walmart.com/", enabled=False),
        RetailSite("costco_us", "Costco", "https://www.costco.com/", enabled=False),
        RetailSite("amazon_us", "Amazon", "https://www.amazon.com/", enabled=True),
    ],
)


# ==================== 墨西哥线（北美洲 / AM）====================
# TV 电视线。SPEC 从 4 个品牌官网抓；价格 + 网评在零售站同一商品页同步抓。
# 与加拿大/美国线结构一致，但只做 TCL/Samsung/LG/Hisense 四个品牌、六个墨西哥零售站。
# 数据独立落 data/mx/（独立库 overseas_mx.db），不与其它线数据重叠。
# 强防护零售站（Amazon/Walmart）默认先关，适配器实现 + 低频小样本验证通过后再开。
_MX = Region(
    code="mx",
    name="墨西哥",
    currency="MXN",
    locale="es-MX",
    enabled=False,   # 4 品牌官网 + 零售站适配器逐步接入并验证后置 True
    note="北美洲墨西哥 TV 线：SPEC 走 4 品牌官网，价格+网评在零售站同一商品页同步抓",
    brands=[
        Brand("tcl_mx", "TCL"),
        Brand("samsung_mx", "Samsung"),
        Brand("lg_mx", "LG"),
        Brand("hisense_mx", "Hisense"),
    ],
    spec_sites=[
        SpecSite("tcl_mx", "tcl_mx", "TCL", "https://www.tcl.com/mx/es"),
        SpecSite("samsung_mx", "samsung_mx", "Samsung", "https://www.samsung.com/mx/tvs/all-tvs/"),
        SpecSite("lg_mx", "lg_mx", "LG", "https://www.lg.com/mx/televisores"),
        SpecSite("hisense_mx", "hisense_mx", "Hisense", "https://hisense.com.mx/televisores"),
    ],
    retail_sites=[
        # 强防护站（Amazon/Walmart）默认先关，各自适配器实现 + 低频小样本验证
        # 通过后再逐个 enabled=True。其余零售站先开，供小样打通链路。
        RetailSite("amazon_mx", "Amazon", "https://www.amazon.com.mx/", enabled=False),
        RetailSite("liverpool_mx", "Liverpool", "https://www.liverpool.com.mx/tienda/home", enabled=True),
        RetailSite("sams_mx", "Sam's", "https://www.sams.com.mx/", enabled=True),
        RetailSite("palacio_mx", "Palacio", "https://www.elpalaciodehierro.com/buscar?q=tv", enabled=True),
        RetailSite("walmart_mx", "Walmart", "https://www.walmart.com.mx/", enabled=False),
        RetailSite("coppel_mx", "Coppel", "https://www.coppel.com/sd/TV", enabled=True),
    ],
)


# ==================== 南美 TV 线（零售：价格 + 网评）====================
# 欧洲线只有 Spec 官网（Samsung/LG/Sony/TCL/Philips），无价格+网评站；南美才是零售线。
# 各国家按「该国有在售型号」对应品牌 + 该国家可访问零售站注册。
# 品牌 code 与 SPEC 明细（已完成站点明细.md）一致（samsung_pe 等），价格在 RetailSpec 顶层声明币种。
# 注：retail_sites 只是「该国家名义上的零售站」登记；实际小样本验证通过后才 enabled=True。

_PE = Region(
    code="pe",
    name="秘鲁",
    currency="PEN",
    locale="es-PE",
    enabled=True,
    note="南美秘鲁 TV 线：价格+网评走秘鲁零售站（Hiraoka 等）",
    brands=[
        Brand("samsung_pe", "Samsung"),
        Brand("lg_pe", "LG"),
        Brand("tcl_pe", "TCL"),
    ],
    retail_sites=[
        RetailSite("hiraoka_pe", "Hiraoka", "https://hiraoka.com.pe/", enabled=True),
        RetailSite("carsa_pe", "Carsa", "https://www.carsa.pe/", enabled=True),
        RetailSite("estilos_pe", "Estilos", "https://www.estilos.com.pe/", enabled=True),
        RetailSite("metro_pe", "Metro", "https://www.metro.pe/", enabled=True),
        RetailSite("credivargas_pe", "Credivargas", "https://www.credivargas.pe/", enabled=True),
        RetailSite("efe_pe", "Efe", "https://www.efe.com.pe/", enabled=True),
        RetailSite("oechsle_pe", "Oechsle", "https://www.oechsle.pe/", enabled=True),
        RetailSite("lacuracao_pe", "La Curacao", "https://www.lacuracao.pe/", enabled=True),
        RetailSite("plazavea_pe", "Plaza Vea", "https://www.plazavea.com.pe/", enabled=True),
        RetailSite("mercadolibre_pe", "Mercado Libre", "https://www.mercadolibre.com.pe/", enabled=True),
    ],
)


# ==================== 南美其他 TV 线（零售：价格 + 网评）====================
_EC = Region(
    code="ec", name="厄瓜多尔", currency="USD", locale="es-EC", enabled=True,
    note="南美厄瓜多尔 TV 线：价格+网评走厄瓜多尔零售站",
    brands=[Brand("samsung_ec", "Samsung"), Brand("hisense_ec", "Hisense"), Brand("tcl_ec", "TCL")],
    retail_sites=[
        RetailSite("cresa_ec", "Cresa", "https://www.cresa.com/", enabled=True),
        RetailSite("japon_ec", "Japon", "https://www.almacenesjapon.com/", enabled=True),
        RetailSite("orvehogar_ec", "Orvehogar", "https://www.orvehogar.com/", enabled=True),
        RetailSite("artefacta_ec", "Artefacta", "https://www.artefacta.com/", enabled=True),
        RetailSite("marcimex_ec", "Marcimex", "https://www.marcimex.com/", enabled=True),
        RetailSite("laganga_ec", "L-Ganga", "https://laganga.com/", enabled=True),
        RetailSite("comandato_ec", "Comandato", "https://www.comandato.com/", enabled=True),
        RetailSite("jaher_ec", "Jaher", "https://www.jaher.com.ec/", enabled=True),
        RetailSite("espana_ec", "Espana", "https://almacenesespana.ec/", enabled=True),
        RetailSite("megamaxi_ec", "Megamaxi", "https://www.megamaxi.com/", enabled=True),
        RetailSite("sukasa_ec", "Sukasa", "https://www.sukasa.com/", enabled=True),
        RetailSite("todohogar_ec", "Todohogar", "https://www.todohogar.com/", enabled=True),
        RetailSite("coral_ec", "Coral", "https://coralhipermercados.com/", enabled=True),
        RetailSite("ferrisariato_ec", "Ferrisariato", "https://www.ferrisariato.com/", enabled=True),
    ],
)

_CO = Region(
    code="co", name="哥伦比亚", currency="COP", locale="es-CO", enabled=True,
    note="南美哥伦比亚 TV 线：价格+网评走哥伦比亚零售站",
    brands=[Brand("samsung_co", "Samsung"), Brand("lg_co", "LG")],
    retail_sites=[
        RetailSite("alkosto_co", "Alkosto", "https://www.alkosto.com/", enabled=True),
        RetailSite("exito_co", "Exito", "https://www.exito.com/", enabled=True),
        RetailSite("falabella_co", "Falabella", "https://www.falabella.com.co/", enabled=True),
        RetailSite("jumbo_co", "Jumbo", "https://www.jumbocolombia.com/", enabled=True),
        RetailSite("olimpica_co", "Olimpica", "https://www.olimpica.com.co/", enabled=False),
    ],
)

_CL = Region(
    code="cl", name="智利", currency="CLP", locale="es-CL", enabled=True,
    note="南美智利 TV 线：价格+网评走智利零售站",
    brands=[Brand("samsung_cl", "Samsung"), Brand("lg_cl", "LG"), Brand("tcl_cl", "TCL")],
    retail_sites=[
        RetailSite("falabella_cl", "Falabella", "https://www.falabella.com/", enabled=True),
        RetailSite("paris_cl", "Paris", "https://www.paris.cl/", enabled=False),
        RetailSite("ripley_cl", "Ripley", "https://simple.ripley.cl/", enabled=False),
        RetailSite("mercadolibre_cl", "Mercado Libre", "https://www.mercadolibre.cl/", enabled=True),
        RetailSite("lider_cl", "Lider", "https://www.lider.cl/", enabled=True),
        RetailSite("abc_cl", "ABC", "https://www.abc.cl/", enabled=True),
        RetailSite("hites_cl", "Hites", "https://www.hites.com/", enabled=True),
    ],
)

_BO = Region(
    code="bo", name="玻利维亚", currency="BOB", locale="es-BO", enabled=True,
    note="南美玻利维亚 TV 线：零售卖站",
    brands=[Brand("samsung_bo", "Samsung"), Brand("tcl_bo", "TCL")],
    retail_sites=[
        RetailSite("multicenter_bo", "Multicenter", "https://www.multicenter.com/", enabled=True),
        RetailSite("dismac_bo", "Dismac", "https://www.dismac.com.bo/", enabled=True),
        RetailSite("alkostoplus_bo", "Alkosto+", "https://alkostoplus.com/", enabled=True),
    ],
)

_AR = Region(
    code="ar", name="阿根廷", currency="ARS", locale="es-AR", enabled=True,
    note="南美阿根廷 TV 线：零售（当前只有 TCL 官网）",
    brands=[Brand("tcl_ar", "TCL")],
    retail_sites=[],
)

# 日本线仍在 api/regions.py 保留其既有结构。
REGIONS: dict[str, Region] = {
    "ca": _CA,
    "us": _US,
    "mx": _MX,
    "pe": _PE,
    "ec": _EC,
    "co": _CO,
    "cl": _CL,
    "bo": _BO,
    "ar": _AR,
}


def get_region(code: str) -> Region | None:
    return REGIONS.get((code or "").strip().lower())


def region_meta(code: str) -> dict[str, Any]:
    """给前端/API 的地区元数据（纯 dict，便于 JSON 序列化）。"""
    r = get_region(code)
    if r is None:
        return {}
    return {
        "code": r.code,
        "name": r.name,
        "currency": r.currency,
        "locale": r.locale,
        "enabled": r.enabled,
        "note": r.note,
        "brands": [{"code": b.code, "name": b.name, "search_name": b.query_name}
                   for b in r.brands],
        "spec_sites": [{"site": s.site, "brand": s.brand, "name": s.name,
                        "probe_url": s.probe_url} for s in r.spec_sites],
        "retail_sites": [{"site": r2.site, "name": r2.name, "probe_url": r2.probe_url,
                          "enabled": r2.enabled} for r2 in r.retail_sites],
    }
