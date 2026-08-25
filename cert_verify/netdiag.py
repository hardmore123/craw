"""分层网络诊断：说清"为什么浏览器能打开、脚本却不行"。

只报"不可达"没用，得分清是哪一层出问题，因为处理办法完全不同：

  L2 TCP 连不上          → 端口/路由被封，或域名被 DNS 投毒
  L3 收到证书但根不受信任  → 中间人（公司网关 SSL 审查），或服务器少发中间证书
  L3 关闭校验后握手超时    → 网关只回自己的证书、不放行上游 ⇒ 域名不在白名单
  L4 HTTP 403 / 拦截页    → 反爬，或网关的 URL 过滤页

最后会检查"浏览器是否有自己的出口"（系统代理 / PAC / 带 proxy 权限的扩展）——
这是"浏览器能上但脚本不能"最常见的原因。

    python -m cert_verify.cli netdiag
    python -m cert_verify.cli netdiag --host www.vde.com
"""
from __future__ import annotations

import glob
import json
import os
import socket
import ssl
import subprocess
import sys

DEFAULT_HOSTS = ["www.certipedia.com", "www.vde.com", "certificates.iecee.org",
                 "productiq.ul.com"]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def _tcp(host: str, port: int = 443, timeout: float = 8) -> tuple[bool, str]:
    s = socket.socket()
    s.settimeout(timeout)
    try:
        import time
        t0 = time.time()
        s.connect((host, port))
        return True, f"{(time.time() - t0) * 1000:.0f} ms"
    except Exception as e:
        return False, f"{type(e).__name__}"
    finally:
        s.close()


def _tls(host: str, verify: bool, timeout: float = 12) -> tuple[str, str]:
    """返回 (结果, 证书签发者)"""
    ctx = ssl.create_default_context() if verify else ssl._create_unverified_context()
    try:
        with socket.create_connection((host, 443), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ss:
                info = ss.getpeercert() or {}
                issuer = dict(x[0] for x in info.get("issuer", ())).get("commonName", "?")
                return f"OK {ss.version()}", issuer
    except ssl.SSLCertVerificationError as e:
        return f"证书不受信任({e.verify_message or e.reason})", ""
    except Exception as e:
        return f"{type(e).__name__}", ""


def diagnose_host(host: str) -> list[str]:
    out = [f"■ {host}"]

    try:
        ips = socket.gethostbyname_ex(host)[2]
        out.append(f"    DNS      {', '.join(ips[:3])}")
    except Exception as e:
        out.append(f"    DNS      解析失败 {type(e).__name__} → 域名被屏蔽或网络不通")
        return out

    ok, detail = _tcp(host)
    out.append(f"    TCP:443  {'连上 ' + detail if ok else '失败 ' + detail}")
    if not ok:
        out.append("    结论     端口/路由层被封")
        return out

    v_res, v_iss = _tls(host, verify=True)
    out.append(f"    TLS校验   {v_res}" + (f"   签发者={v_iss}" if v_iss else ""))

    if v_res.startswith("OK"):
        out.append("    结论     正常，脚本可直接抓")
        return out

    u_res, u_iss = _tls(host, verify=False)
    out.append(f"    TLS不校验 {u_res}" + (f"   签发者={u_iss}" if u_iss else ""))

    # 直连不行时，试试代理（VPN/加速器的本地端口）能不能穿透
    from .http_client import resolve_proxy
    proxy = resolve_proxy()
    if not u_res.startswith("OK") and proxy:
        p_ok, p_detail = _via_proxy(host, proxy)
        out.append(f"    经代理    {'成功 ' + p_detail if p_ok else '失败 ' + p_detail}"
                   f"   （{proxy}）")
        if p_ok:
            out.append(f"    结论     直连被网关拦，但经代理可通 ⇒ 用 --proxy {proxy} "
                       "或设 CERT_PROXY 即可抓")
            return out

    if u_res.startswith("OK"):
        out.append("    结论     证书链有问题但连接可用 —— 多为服务器少发中间证书，"
                   "或本机缺企业根 CA")
    else:
        tail = "；已尝试系统代理仍不通" if proxy else "；未发现可用代理，可开 VPN/加速器后重试"
        out.append("    结论     网关回了自己的证书就不放行上游 ⇒ 该域名被拦" + tail)
    return out


def _via_proxy(host: str, proxy: str, timeout: float = 20) -> tuple[bool, str]:
    """经指定代理请求该域名首页，判断能否穿透。"""
    import urllib.request
    try:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        req = urllib.request.Request(f"https://{host}/", headers={"User-Agent": UA})
        with opener.open(req, timeout=timeout) as r:
            return True, f"HTTP {r.status}"
    except Exception as e:
        return False, type(e).__name__


def browser_egress() -> list[str]:
    """浏览器是否有独立于系统的出口 —— '浏览器能上、脚本不能' 的根源。"""
    out = ["■ 浏览器是否有自己的出口"]

    # 系统代理
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")

        def rd(name):
            try:
                return winreg.QueryValueEx(k, name)[0]
            except OSError:
                return ""
        out.append(f"    系统代理  ProxyEnable={rd('ProxyEnable')} "
                   f"ProxyServer={rd('ProxyServer') or '-'} "
                   f"PAC={rd('AutoConfigURL') or '-'}")
    except Exception as e:
        out.append(f"    系统代理  读取失败 {type(e).__name__}")

    # 本地代理端口
    listening = []
    for port in (7877, 7890, 7891, 1080, 10808, 10809, 8080, 8888, 33210, 7897):
        s = socket.socket()
        s.settimeout(0.4)
        try:
            s.connect(("127.0.0.1", port))
            listening.append(port)
        except Exception:
            pass
        finally:
            s.close()
    out.append(f"    本地代理端口  {listening or '无（常见端口都没在听）'}")

    # 带 proxy 权限的浏览器扩展
    hits = []
    roots = [
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\User Data"),
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data"),
    ]
    for root in roots:
        for mf in glob.glob(os.path.join(root, "*", "Extensions", "*", "*", "manifest.json")):
            try:
                with open(mf, "r", encoding="utf-8", errors="replace") as f:
                    m = json.load(f)
            except Exception:
                continue
            perms = m.get("permissions", []) or []
            if "proxy" not in [str(p).lower() for p in perms]:
                continue
            name = str(m.get("name", ""))
            ext_id = os.path.basename(os.path.dirname(os.path.dirname(mf)))
            if name.startswith("__MSG_"):        # 本地化名字，去 _locales 里取
                for loc in ("zh_CN", "en", "zh"):
                    p = os.path.join(os.path.dirname(mf), "_locales", loc, "messages.json")
                    if os.path.exists(p):
                        try:
                            with open(p, "r", encoding="utf-8", errors="replace") as f:
                                msgs = json.load(f)
                            for key in ("appName", "extName", "name", "app_name"):
                                if key in msgs:
                                    name = msgs[key].get("message", name)
                                    break
                            if name.startswith("__MSG_"):
                                about = msgs.get("about_website", {}).get("message", "")
                                if about:
                                    name = about
                        except Exception:
                            pass
                        break
            hits.append(f"{name}  ({ext_id})")
    if hits:
        out.append("    ⚠ 有 proxy 权限的浏览器扩展：")
        for h in sorted(set(hits)):
            out.append(f"        {h}")
        out.append("      → 这类扩展只代理浏览器内的流量。浏览器能打开、脚本打不开，"
                   "通常就是这个原因。")
    else:
        out.append("    带 proxy 权限的扩展  无")
    return out


def diagnose(hosts: list[str] | None = None) -> str:
    hosts = hosts or DEFAULT_HOSTS
    blocks = []
    for h in hosts:
        blocks.append("\n".join(diagnose_host(h)))
    blocks.append("\n".join(browser_egress()))
    text = "分层网络诊断\n" + "\n\n".join(blocks)
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "netdiag_report.txt")
    try:
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        text += f"\n\n（已保存到 {p}）"
    except Exception:
        pass
    return text


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(diagnose(sys.argv[1:] or None))
