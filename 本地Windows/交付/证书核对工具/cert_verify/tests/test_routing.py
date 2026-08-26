"""路由规则测试：按认证类别 / 证书号前缀选网站，并验证证书号归一化。

规则：认证类别含 UL（UL、CB/UL）→ UL 网站；纯 CB → VDE 网站。
证书号前缀：'UL E67006'→'E67006'（UL keyword）；'VDE 40018250'→'40018250'（VDE certificate No.）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from cert_verify.providers import ProviderRouter  # noqa: E402
from cert_verify.providers.ul import UlSession  # noqa: E402
from cert_verify.providers.vde import VdeProvider  # noqa: E402

_fail = 0


def check(name, cond):
    global _fail
    print(f"  [{'OK ' if cond else 'FAIL'}] {name}")
    if not cond:
        _fail += 1


r = ProviderRouter()

print("=== 1. 按认证类别列路由 ===")
check("认证类别 CB → VDE", r.resolve("VDE 40018250", "CB").name == "VDE")
check("认证类别 UL → UL", r.resolve("UL E67006", "UL").name == "UL")
check("认证类别 CB/UL → UL", r.resolve("E502081", "CB/UL").name == "UL")
check("认证类别 UL/CB → UL", r.resolve("E502081", "UL/CB").name == "UL")
check("认证类别 CB-UL → UL", r.resolve("E502081", "CB-UL").name == "UL")

print("=== 2. 证书号显式前缀优先于认证类别 ===")
# 认证类别是 CB，但证书号写了 TUV → 应去 TÜV（前缀优先）
check("类别CB + 'TUV R50151509' → TÜV", r.resolve("TUV R50151509", "CB").name == "TUV-Rheinland")
check("类别CB + 'VDE 40018250' → VDE", r.resolve("VDE 40018250", "CB").name == "VDE")
check("类别CB + 'UL E67006' → UL", r.resolve("UL E67006", "CB").name == "UL")
check("'ENEC-02099' → 不搜索(None)", r.resolve("ENEC-02099", "CB") is None)
check("类别CB 无前缀纯数字 → VDE", r.resolve("40018250", "CB").name == "VDE")

print("=== 3. 无提示时按证书号形态 ===")
check("E67006 → UL", r.resolve("E67006", "").name == "UL")
check("40018250 → VDE", r.resolve("40018250", "").name == "VDE")

print("=== 4. TÜV 不受影响 ===")
check("TUV 提示 → TÜV", r.resolve("R50151509", "TUV").name == "TUV-Rheinland")

print("=== 5. 证书号归一化（去前缀）===")
check("UL 去前缀: 'UL E67006'→'E67006'", UlSession._norm_no("UL E67006") == "E67006")
check("UL 无前缀保持: 'E502081'", UlSession._norm_no("E502081") == "E502081")
check("VDE 去前缀: 'VDE 40018250'→'40018250'", VdeProvider._normalize("VDE 40018250") == "40018250")
check("VDE 无前缀保持: '40018250'", VdeProvider._normalize("40018250") == "40018250")

r.close()
print("\n" + ("全部通过" if _fail == 0 else f"{_fail} 项失败"))
sys.exit(1 if _fail else 0)
