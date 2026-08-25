"""TUV Rheinland — Certipedia (www.certipedia.com)

抓取路径（已实测可用，纯 HTTP，无需浏览器）：

1. 证书详情直连：
   GET /certificates/<数字证书号>?locale=en
   例：https://www.certipedia.com/certificates/50679826?locale=en

2. 证书号带前缀/查不到时，走搜索兜底：
   GET /search/matching_product_certificates?locale=en&q=<证书号>
   结果行里取 <a href="/certificates/<no>?..."> 或
   <a href="/quality_marks/<id>?locale=en&certificate_number=<no>">

页面字段（实测）：
   Certificate Holder / Certificate Number / Order Number /
   Certified Product（内含 "Model Designation:"）/ Fulfilled Standards /
   Date of Issue / Certificate Type
证书状态在 <div class="certificate-status-container valid"> +
   <h2 class="... certificate-status">Valid Certificate</h2>

注意：Certipedia 不公开"生产厂 / Manufacturing plant"，该字段只能返回空，
比对时按"网站无此项"处理，不判为不一致。
"""
from __future__ import annotations

import re

from ..http_client import HttpClient
from ..models import ERROR, FOUND, NOT_FOUND, BLOCKED, CertRecord
from .base import BaseProvider, text_of

BASE = "https://www.certipedia.com"

# 例：R 50264406 / S 50543817 / JQ 60056789 / 50679826
_NUM_RE = re.compile(r"(\d{7,12})")

# label 用 [^<]* 限制在单个元素内，否则会把上方"Valid Certificate"状态块一起吞进来
_ITEM_RE = re.compile(
    r'class="[^"]*tuv-list__item__header[^"]*"[^>]*>(?P<label>[^<]*)</(?:div|h\d)>\s*'
    r'<div class="tuv-list__item__text">(?P<value>.*?)</div>\s*</li>',
    re.S | re.I,
)

_LABEL_MAP = {
    "certificate holder": "manufacturer",
    "zertifikatsinhaber": "manufacturer",
    "certificate number": "cert_no_on_site",
    "zertifikatsnummer": "cert_no_on_site",
    "certified product": "_product",
    "fulfilled standards": "standard",
    "erfüllte normen": "standard",
    "date of issue": "issue_date",
    "ausstellungsdatum": "issue_date",
    "valid until": "expiry_date",
    "date of expiry": "expiry_date",
    "certificate type": "cert_type",
    "manufacturing plant": "factory",
    "production site": "factory",
}


class TuvRheinlandProvider(BaseProvider):
    name = "TUV-Rheinland"
    number_patterns = (
        r"^[A-Z]{1,3}\s*\d{7,10}$",   # R 50264406 / JQ 60056789
        r"^\d{8}$",                    # 50679826
    )

    def __init__(self, client: HttpClient | None = None):
        self.client = client or HttpClient()

    # ---------- 主流程 ----------
    def query(self, cert_no: str) -> CertRecord:
        rec = CertRecord(query_no=cert_no, source=self.name)
        digits = self._digits(cert_no)
        if not digits:
            rec.status = NOT_FOUND
            rec.message = "证书号中未找到有效数字段"
            return rec

        url = f"{BASE}/certificates/{digits}?locale=en"
        resp = self.client.get(url)

        if resp.blocked:
            rec.status, rec.message = BLOCKED, "被网络策略拦截（URL 过滤）"
            return rec
        if resp.status == -1:
            rec.status, rec.message = ERROR, resp.text[:120]
            return rec

        if resp.status == 404 or "tuv-list__item__header" not in resp.text:
            # 兜底：走搜索
            hit = self._search(cert_no) or self._search(digits)
            if not hit:
                rec.status = NOT_FOUND
                rec.message = "Certipedia 未检索到该证书号"
                return rec
            url = hit
            resp = self.client.get(url)
            if resp.status != 200:
                rec.status, rec.message = ERROR, f"详情页 HTTP {resp.status}"
                return rec

        self._parse(resp.text, rec)
        rec.source_url = url
        rec.status = FOUND if (rec.manufacturer or rec.standard or rec.cert_no_on_site) else NOT_FOUND
        if rec.status == NOT_FOUND:
            rec.message = "详情页无证书字段"
        return rec

    @staticmethod
    def _digits(cert_no: str) -> str:
        """从 'R 50264406' / 'S 50543817' / '50679826' 里取出数字证书号。"""
        m = _NUM_RE.search((cert_no or "").replace(" ", ""))
        return m.group(1) if m else ""

    # ---------- 搜索兜底 ----------
    def _search(self, q: str) -> str:
        if not q:
            return ""
        url = f"{BASE}/search/matching_product_certificates?locale=en&q={q.replace(' ', '+')}"
        resp = self.client.get(url)
        if resp.status != 200 or resp.blocked:
            return ""
        m = re.search(r'href="(/certificates/\d+[^"]*)"', resp.text)
        if m:
            return BASE + m.group(1).replace("&amp;", "&")
        # 从 quality_marks 链接里取 certificate_number
        m = re.search(r'/quality_marks/\d+\?[^"]*certificate_number=(\d+)', resp.text)
        if m:
            return f"{BASE}/certificates/{m.group(1)}?locale=en"
        return ""

    # ---------- 解析 ----------
    def _parse(self, html_text: str, rec: CertRecord) -> None:
        for m in _ITEM_RE.finditer(html_text):
            label = text_of(m.group("label")).strip().rstrip(":")
            value = text_of(m.group("value"), keep_lines=True)
            if not label:
                continue
            rec.raw_fields[label] = value
            key = _LABEL_MAP.get(label.lower())
            if key == "_product":
                rec.model = self._extract_model(value)
                continue
            if key == "standard":
                rec.standard = self._clean_standards(value)
                continue
            if key == "manufacturer":
                # Certipedia "Certificate Holder" 常含地址（多行：公司名 \n 地址1 \n 地址2...）
                # 只取第一行作为制造商名称，去掉后续地址行
                rec.manufacturer = self._extract_company_name(value)
                continue
            if key:
                setattr(rec, key, value)

        rec.cert_status = self._extract_status(html_text)

    @staticmethod
    def _extract_company_name(value: str) -> str:
        """从 Certificate Holder 里提取公司名，去掉地址。

        Certipedia 格式通常是：
          CONQUER ELECTRONICS CO., LTD
          No. 28 Wuquan 5th Road, 248 Wugu District, New Taipei City Taiwan

        策略：按换行分割，第一行是公司名。
        """
        lines = [ln.strip() for ln in value.split("\n") if ln.strip()]
        if not lines:
            return value.strip()
        return lines[0]

    @staticmethod
    def _extract_model(product_text: str) -> str:
        """Certified Product 里形如：
        'External Switching PSU (AC ADAPTER)\nModel Designation: A24-065N3A (...)'
        """
        m = re.search(r"(?:Model\s*Designation|Typenbezeichnung)\s*:?\s*(.+)",
                      product_text, re.S | re.I)
        if m:
            return " ".join(m.group(1).split())
        return " ".join(product_text.split())

    @staticmethod
    def _clean_standards(value: str) -> str:
        """去掉页面尾部免责声明，只留标准号，多个用 ; 连接。"""
        lines = []
        for ln in value.split("\n"):
            ln = ln.strip()
            if not ln:
                continue
            if re.search(r"standard\(s\) listed here|status at the time", ln, re.I):
                break
            lines.append(ln)
        return "; ".join(lines)

    @staticmethod
    def _extract_status(html_text: str) -> str:
        m = re.search(r'certificate-status-container\s+([a-z_]+)', html_text, re.I)
        cls = (m.group(1) if m else "").lower()
        m2 = re.search(r'certificate-status[^>]*>\s*(.*?)\s*</h2>', html_text, re.S | re.I)
        label = text_of(m2.group(1)) if m2 else ""
        if label:
            return label                      # 'Valid Certificate' / 'Withdrawn' ...
        return {"valid": "Valid Certificate"}.get(cls, cls or "")
