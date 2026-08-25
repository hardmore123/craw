r"""VDE — 直连官方后端检索接口（Solr REST），免登录

## 接口

页面 https://www.vde.com/tic-en/marks-and-certificates/vde-approved-products/search
背后调的是一个 Solr 查询接口：

    GET https://www.vde.com/www8/servlet/restSearch/2289904/zertreg-certificates
        ?wt=json
        &fq=(detailDocType:zertreg|certificate)
        &fq=id:zertreg|certificate#<证书号>
        &start=0&rows=50

必要请求头只有两个：`Accept: application/json` 和 `Referer`（指向对应的搜索页）。
Cookie 里只有 GA 分析用的值，**不需要登录、不需要 Cookie**。

比爬 HTML 好在：结构化 JSON、字段稳定、一次请求出结果、不用浏览器。

## 已验证的返回字段（来自真实响应 40052271）

| JSON 字段 | 含义 | 映射到 |
|---|---|---|
| `companyName` / `companyName_en` / `companyName_de` | 证书持有人 | 制造商 |
| `typeNames`（数组，完整型号列表） | 型号 | 型号 |
| `firstTypes`（截断的预览串） | 型号预览 | 型号兜底 |
| `id` = `zertreg\|certificate#40052271` | 证书号 | 证书号 |
| `registerNumber` = `E809` | VDE 注册号 Reg.-Nr. | 辅助 |
| `productName` / `productName_en` | 产品类别（Varistor） | 辅助 |
| `validityLimit` | 有效期限 | 到期日 |
| `numFound >= 1` | 见下"证书状态"说明 | 证书状态 |

## 证书状态怎么判

服务端在 fq 里强制加了有效期过滤：

    validfrom: [* TO NOW/MINUTE]
    validto:   [NOW/MINUTE TO *]

也就是**这个接口只返回当前有效的证书**。所以 `numFound >= 1` 即"有效"。
代价是查不到时无法区分"证书不存在"和"证书已失效"，工具会如实写成
"官网未查到（可能已失效或证书号有误）"，不猜。

想区分的话可以试着覆盖这两个参数（`INCLUDE_EXPIRED=True` 会带上
`validfrom=[* TO *]&validto=[* TO *]`）—— 服务端是否允许覆盖需联网后实测。

## 标准和生产厂

接口默认 `fl` 里**没有**标准、生产厂字段。所以：
- 先尝试 `fl=*` 把全部字段取回来（Solr 通常允许用请求参数覆盖 fl）；
- 若拿不到，这两项返回空，比对时记"网站无此项"，不判不一致。
详情页 `generatedUrl` 里可能有标准，联网后用 probe-vde 落盘确认。

## 本机网络

vde.com 被公司网关白名单拦截（TLS 握手直接超时/RST，关闭证书校验也不通）。
所以解析逻辑是对着真实响应离线写并测试的（见 tests/fixtures/vde_40052271.json），
网络一放通即可直接使用。
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse

from ..http_client import HttpClient
from ..models import BLOCKED, ERROR, FOUND, NOT_FOUND, CertRecord
from .base import BaseProvider

API = "https://www.vde.com/www8/servlet/restSearch/2289904/zertreg-certificates"
SEARCH_PAGE = ("https://www.vde.com/tic-en/marks-and-certificates/vde-approved-products/search"
               "?search_t=name&page=1&certificateNumber={cert_no}")
DETAIL_PAGE = ("https://www.vde.com/tic-en/marks-and-certificates/vde-approved-products"
               "/certificate?id={num}&type=zertreg%7Ccertificate")

# 是否尝试把已失效证书也查出来（服务端是否允许覆盖有效期过滤，需联网实测）
INCLUDE_EXPIRED = False

# 可能承载"标准"的字段名（fl=* 生效时才会出现），按优先级取第一个非空
STANDARD_KEYS = ("standardNames", "standards", "normNames", "norms", "testStandards",
                 "specification", "normen", "standard")
# 可能承载"生产厂"的字段名
FACTORY_KEYS = ("manufacturingPlant", "productionSite", "factoryNames", "plantNames",
                "factory", "fertigungsstaette")


class VdeProvider(BaseProvider):
    name = "VDE"
    # VDE 证书号：40052271 / 40040940-4880-0001 / HA012345 / NA123456
    number_patterns = (
        r"^\d{7,8}(-\d+)*$",
        r"^(HA|NA)\s*\d{6,8}$",
    )

    def __init__(self, headless: bool = True, proxy: str | None = None,
                 dump_dir: str | None = None, client: HttpClient | None = None):
        self.proxy = proxy or os.environ.get("VDE_PROXY")
        self.dump_dir = dump_dir
        # 未显式给 client 时，让 HttpClient 自己解析代理（含 Windows 系统代理）
        self.client = client or HttpClient(proxy=self.proxy)
        self.headless = headless          # 保留参数以兼容 ProviderRouter 调用

    # ---------------- URL 构造 ----------------
    @classmethod
    def api_url(cls, cert_no: str, all_fields: bool = True) -> str:
        num = cls._normalize(cert_no)
        params = [
            ("wt", "json"),
            ("fq", "(detailDocType:zertreg|certificate)"),
            ("fq", f"id:zertreg|certificate#{num}"),
            ("start", "0"),
            ("rows", "50"),
        ]
        if all_fields:
            params.append(("fl", "*"))      # 试着把标准/生产厂等字段一起取回来
        if INCLUDE_EXPIRED:
            params += [("validfrom", "[* TO *]"), ("validto", "[* TO *]")]
        return API + "?" + urllib.parse.urlencode(params, safe="")

    @classmethod
    def _headers(cls, cert_no: str) -> dict:
        return {
            "Accept": "application/json, text/plain, */*",
            "Referer": SEARCH_PAGE.format(cert_no=cls._normalize(cert_no)),
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }

    # ---------------- 主流程 ----------------
    def query(self, cert_no: str) -> CertRecord:
        rec = CertRecord(query_no=cert_no, source=self.name)
        num = self._normalize(cert_no)
        if not num:
            rec.status, rec.message = NOT_FOUND, "证书号为空"
            return rec

        rec.source_url = DETAIL_PAGE.format(num=num)

        # 先 fl=* 试全字段；失败或字段异常再退回接口默认字段
        for all_fields in (True, False):
            url = self.api_url(cert_no, all_fields=all_fields)
            resp = self.client.get(url, headers=self._headers(cert_no))

            if resp.blocked:
                rec.status = BLOCKED
                rec.message = "vde.com 被公司网络 URL 过滤拦截，需加白名单或配 VDE_PROXY"
                return rec
            if resp.status == -1:
                rec.status = BLOCKED
                rec.message = ("vde.com 不可达（TLS 握手超时/被重置），"
                               "属网关白名单拦截；请 IT 加白名单或设置 VDE_PROXY")
                return rec
            if resp.status != 200:
                if all_fields:
                    continue                      # fl=* 可能被拒，换默认字段再试
                rec.status, rec.message = ERROR, f"接口返回 HTTP {resp.status}"
                return rec

            self._dump(resp.text, f"vde_api_{num}{'_all' if all_fields else ''}")
            try:
                data = json.loads(resp.text)
            except json.JSONDecodeError:
                if all_fields:
                    continue
                rec.status, rec.message = ERROR, "接口返回不是合法 JSON"
                return rec

            return self.parse(data, rec)

        rec.status, rec.message = ERROR, "两种字段模式均请求失败"
        return rec

    # ---------------- 解析（对着真实响应写，可离线测试） ----------------
    @classmethod
    def parse(cls, data: dict, rec: CertRecord | None = None) -> CertRecord:
        rec = rec or CertRecord(source="VDE")
        resp = data.get("response") or {}
        docs = resp.get("docs") or []
        found = resp.get("numFound", len(docs))

        if not docs:
            rec.status = NOT_FOUND
            rec.message = ("官网未查到（该接口只返回有效期内的证书，"
                           "所以也可能是证书已失效或证书号有误）")
            return rec

        doc = docs[0]
        rec.raw_fields = {k: cls._flat(v) for k, v in doc.items()}

        # 制造商：英文优先，其次通用名，最后德文
        rec.manufacturer = cls._first(doc, "companyName_en", "companyName", "companyName_de")

        # 型号：typeNames 是完整列表，firstTypes 只是截断预览
        types = doc.get("typeNames")
        if isinstance(types, list) and types:
            rec.model = "; ".join(str(t).strip() for t in types if str(t).strip())
        else:
            rec.model = cls._first(doc, "firstTypes")

        rec.standard = cls._first(doc, *STANDARD_KEYS)
        rec.factory = cls._first(doc, *FACTORY_KEYS)

        # 证书号：id 形如 zertreg|certificate#40052271
        rec.cert_no_on_site = cls._cert_no_from_id(doc.get("id", "")) or rec.query_no
        rec.expiry_date = cls._first(doc, "validityLimit", "validto")
        rec.issue_date = cls._first(doc, "validfrom", "dateOfIssue")

        # 状态：接口自带有效期过滤，命中即有效
        if INCLUDE_EXPIRED:
            rec.cert_status = ""            # 覆盖了过滤条件，就不能靠命中推断
        else:
            rec.cert_status = "Valid Certificate"

        # 辅助信息塞进 raw，便于人工复核
        reg = cls._first(doc, "registerNumber")
        product = cls._first(doc, "productName_en", "productName", "productName_de")
        if reg:
            rec.raw_fields["__注册号 Reg.-Nr.__"] = reg
        if product:
            rec.raw_fields["__产品类别__"] = product
        rec.cert_type = product
        rec.raw_fields["__numFound__"] = str(found)

        url = doc.get("generatedUrl")
        if url:
            rec.source_url = "https://www.vde.com" + url

        rec.status = FOUND
        return rec

    # ---------------- 工具 ----------------
    @staticmethod
    def _flat(v) -> str:
        if isinstance(v, list):
            return "; ".join(str(x) for x in v)
        return str(v)

    @classmethod
    def _first(cls, doc: dict, *keys: str) -> str:
        for k in keys:
            v = doc.get(k)
            if v in (None, "", [], {}):
                continue
            s = cls._flat(v).strip()
            if s:
                return s
        return ""

    @staticmethod
    def _cert_no_from_id(doc_id: str) -> str:
        m = re.search(r"#(\S+)$", str(doc_id or ""))
        return m.group(1) if m else ""

    @staticmethod
    def _normalize(cert_no: str) -> str:
        """归一化证书号：
        - 去掉机构前缀（表格 I 列写作 'VDE 40018250'）
        - VDE 官方提示：带 HA / NA 前缀要去字母、保留含前导零的完整数字
        """
        s = (cert_no or "").strip()
        s = re.sub(r"^(?:VDE|TUV|T[ÜU]V|UL|CB)\s*[:：]?\s*", "", s, flags=re.I).strip()
        m = re.match(r"^(?:HA|NA)\s*(\d+)$", s, re.I)
        return m.group(1) if m else s

    def _dump(self, text: str, name: str) -> None:
        if self.dump_dir:
            os.makedirs(self.dump_dir, exist_ok=True)
            with open(os.path.join(self.dump_dir, f"{name}.json"), "w", encoding="utf-8") as f:
                f.write(text)
