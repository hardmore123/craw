"""命令行入口。

    # 0. 网络自检（先跑这个，决定哪几家能抓）
    python -m cert_verify.cli netcheck

    # 1. 单条试抓，确认解析对不对
    python -m cert_verify.cli query 50679826 --agency TUV
    python -m cert_verify.cli query 40040940 --agency VDE

    # 2. 看表格列结构
    python -m cert_verify.cli inspect "xxx.xlsx"

    # 3. 小样跑通再全量
    python -m cert_verify.cli run "xxx.xlsx" --limit 10
    python -m cert_verify.cli run "xxx.xlsx"
"""
from __future__ import annotations

import argparse
import json
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def cmd_netcheck(args):
    from .netcheck import check
    print(check())


def cmd_netdiag(args):
    from .netdiag import diagnose
    print(diagnose(args.host or None))


def cmd_query(args):
    from .providers import ProviderRouter
    rec = ProviderRouter(headless=not args.show_browser,
                         proxy=getattr(args, "proxy", None)).query(args.cert_no, args.agency)
    print(json.dumps({
        "query_no": rec.query_no, "source": rec.source, "status": rec.status,
        "message": rec.message, "manufacturer": rec.manufacturer, "factory": rec.factory,
        "model": rec.model, "standard": rec.standard, "cert_status": rec.cert_status,
        "cert_no_on_site": rec.cert_no_on_site, "issue_date": rec.issue_date,
        "expiry_date": rec.expiry_date, "cert_type": rec.cert_type[:80],
        "source_url": rec.source_url, "raw_fields": {k: v[:120] for k, v in rec.raw_fields.items()},
    }, ensure_ascii=False, indent=2))


def cmd_inspect(args):
    from .excel_runner import inspect_workbook
    print(inspect_workbook(args.xlsx, args.header_rows))


def cmd_run(args):
    from .config import RunConfig
    from .excel_runner import run, summarize
    cfg = RunConfig(
        xlsx_path=args.xlsx,
        output_path=args.out,
        sheets=args.sheet or [],
        limit=args.limit,
        headless=not args.show_browser,
        default_agency=args.agency,
        write_detail_col=args.detail_col,
        proxy=getattr(args, "proxy", None),
    )
    print(summarize(run(cfg)))


def cmd_probe_vde(args):
    """VDE 联通后跑一次：把接口原始 JSON 落盘，确认 fl=* 能否取到标准/生产厂。"""
    import os
    from .providers.vde import VdeProvider
    dump_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_probe")
    p = VdeProvider(dump_dir=dump_dir)
    print("请求 URL(全字段): " + VdeProvider.api_url(args.cert_no, all_fields=True))
    print("请求 URL(默认)  : " + VdeProvider.api_url(args.cert_no, all_fields=False))
    rec = p.query(args.cert_no)
    print(f"\nstatus={rec.status} message={rec.message}")
    print(f"原始 JSON 落盘目录: {dump_dir}")
    print("\n--- 接口返回字段 ---")
    print(json.dumps(rec.raw_fields, ensure_ascii=False, indent=2)[:5000])
    if not rec.standard:
        print("\n注意：标准字段为空。若 _all.json 里也没有标准相关键，"
              "说明该接口不提供标准，需要另找详情接口。")


def cmd_parse_file(args):
    """离线解析：把手工保存的接口响应喂给解析器。

    VDE 网络没放通时的过渡方案 —— 在浏览器 DevTools 里把
    restSearch 接口的 JSON 响应存成文件，用这个解析，
    规则与在线路径完全一致。
    """
    from .models import CertRecord
    from .providers.vde import VdeProvider

    with open(args.data_file, "r", encoding="utf-8", errors="replace") as f:
        raw = f.read()

    if args.source != "vde":
        raise SystemExit(f"暂不支持 {args.source}")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise SystemExit(
            f"不是合法 JSON（{e}）。请保存 restSearch 接口的响应，而不是网页 HTML。\n"
            f"取法：浏览器 F12 → Network → 过滤 restSearch → 右键 Copy response") from e

    rec = VdeProvider.parse(data, CertRecord(query_no=args.cert_no, source="VDE"))
    print(json.dumps({
        "status": rec.status, "message": rec.message,
        "manufacturer": rec.manufacturer, "factory": rec.factory,
        "model": rec.model, "standard": rec.standard, "cert_status": rec.cert_status,
        "cert_no_on_site": rec.cert_no_on_site, "expiry_date": rec.expiry_date,
        "source_url": rec.source_url,
    }, ensure_ascii=False, indent=2))
    print("\n--- 接口返回的全部字段（用于确认还能取到什么）---")
    print(json.dumps(rec.raw_fields, ensure_ascii=False, indent=2)[:6000])


def main(argv=None):
    ap = argparse.ArgumentParser(prog="cert_verify", description="证书清单与官网查询结果一致性核对")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("netcheck", help="检查各认证机构网站可达性").set_defaults(func=cmd_netcheck)

    nd = sub.add_parser("netdiag", help="分层诊断：为什么浏览器能上、脚本不能")
    nd.add_argument("--host", action="append", help="指定域名，可多次；默认查四家")
    nd.set_defaults(func=cmd_netdiag)

    q = sub.add_parser("query", help="查单个证书号")
    q.add_argument("cert_no")
    q.add_argument("--agency", default="", help="TUV / VDE / UL")
    q.add_argument("--proxy", default="", help="代理，如 http://127.0.0.1:7877；留空自动探测系统代理")
    q.add_argument("--show-browser", action="store_true")
    q.set_defaults(func=cmd_query)

    i = sub.add_parser("inspect", help="打印 xlsx 各 sheet 表头，用于确认列映射")
    i.add_argument("xlsx")
    i.add_argument("--header-rows", type=int, default=3)
    i.set_defaults(func=cmd_inspect)

    r = sub.add_parser("run", help="全量核对并写回 M 列")
    r.add_argument("xlsx")
    r.add_argument("--out", default="")
    r.add_argument("--sheet", action="append", help="只跑指定 sheet，可多次指定")
    r.add_argument("--limit", type=int, default=0)
    r.add_argument("--agency", default="", help="表里没有机构列时指定默认机构")
    r.add_argument("--detail-col", default="", help="把来源 URL 写到该列，如 N")
    r.add_argument("--proxy", default="", help="代理，如 http://127.0.0.1:7877；留空自动探测系统代理")
    r.add_argument("--show-browser", action="store_true")
    r.set_defaults(func=cmd_run)

    pv = sub.add_parser("probe-vde", help="VDE 联通后落盘 DOM / XHR 接口，标定解析规则")
    pv.add_argument("--cert-no", default="40052271")
    pv.add_argument("--show-browser", action="store_true")
    pv.set_defaults(func=cmd_probe_vde)

    pf = sub.add_parser("parse-file", help="离线解析手工保存的接口 JSON 响应")
    pf.add_argument("source", choices=["vde"])
    pf.add_argument("data_file")
    pf.add_argument("--cert-no", default="")
    pf.set_defaults(func=cmd_parse_file)

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
