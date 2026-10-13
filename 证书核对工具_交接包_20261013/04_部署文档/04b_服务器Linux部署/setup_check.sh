#!/usr/bin/env bash
# 证书核对工具 · 服务器环境准备与自检（清单第 2~5 步）
#
# 作用：拉/更新代码 → 建 venv → 装依赖 → 查路径可写 → 查解密服务。
# 幂等：可重复执行。不改 systemd、不改 Nginx（那两步见 服务器部署清单.md 第 6~9 步）。
#
# 用法（在服务器上，用 hisense 用户执行）：
#   bash /home/hisense/pro/Gitlab/DataGather/Linux/deploy/setup_check.sh
# 首次还没有代码时，先手动 clone（见脚本顶部提示），或直接跑本脚本自动 clone。

set -u

PROJ="/home/hisense/pro/Gitlab/DataGather"
REPO="http://10.18.203.174/full-stack-frontend/hisense/agents/ai_applications/craw.git"
DECRYPT_URL="http://10.18.87.51:8500/"

ok()   { printf "  [OK ] %s\n" "$1"; }
warn() { printf "  [!!] %s\n" "$1"; }
step() { printf "\n==== %s ====\n" "$1"; }

# ---- 2. 获取/更新代码 ----
step "2. 获取/更新代码"
if [ -d "$PROJ/.git" ]; then
  cd "$PROJ" && git pull && ok "git pull 完成"
elif [ -d "$PROJ" ] && [ -e "$PROJ/cert_verify/web/__main__.py" ]; then
  ok "已有代码（非 git，跳过拉取）"
  cd "$PROJ"
else
  mkdir -p /home/hisense/pro/Gitlab
  git clone "$REPO" "$PROJ" && ok "git clone 完成" || { warn "clone 失败，请手动放置代码到 $PROJ"; exit 1; }
  cd "$PROJ"
fi

if [ -e "$PROJ/cert_verify/web/__main__.py" ]; then
  ok "网页入口存在：cert_verify/web/__main__.py"
else
  warn "找不到 cert_verify/web/__main__.py —— 代码结构不对，检查是否套了多层同名目录"
  exit 1
fi

# ---- 3. venv + 依赖 ----
step "3. 虚拟环境 + 依赖"
if [ ! -x "$PROJ/venv/bin/python" ]; then
  python3 -m venv "$PROJ/venv" && ok "venv 已创建"
else
  ok "venv 已存在"
fi
"$PROJ/venv/bin/python" -m pip install --upgrade pip -q
"$PROJ/venv/bin/pip" install -q -r "$PROJ/cert_verify/requirements.txt"
"$PROJ/venv/bin/python" - <<'PY'
import openpyxl
import cert_verify.web.server  # noqa
print("  [OK ] openpyxl", openpyxl.__version__, "; cert_verify.web 可导入")
PY

# ---- 4. 路径与权限 ----
step "4. 路径与权限"
OWNER=$(stat -c '%U' "$PROJ")
[ "$OWNER" = "hisense" ] && ok "项目归属 hisense" || warn "项目归属是 $OWNER（建议 hisense）"
mkdir -p "$PROJ/cert_verify/web/_work" "$PROJ/cert_verify/.cache"
if touch "$PROJ/cert_verify/web/_work/.wtest" 2>/dev/null; then
  rm -f "$PROJ/cert_verify/web/_work/.wtest"; ok "运行目录可写"
else
  warn "运行目录不可写：$PROJ/cert_verify/web/_work"
fi

# ---- 5. 解密服务 ----
step "5. 解密服务"
if systemctl is-active --quiet decryptor.service 2>/dev/null; then
  ok "decryptor.service active"
else
  warn "decryptor.service 未 active（如非本次范围可忽略，但解密会不可用）"
fi
CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$DECRYPT_URL" || echo "000")
[ "$CODE" = "200" ] && ok "解密服务 HTTP 200：$DECRYPT_URL" || warn "解密服务返回 $CODE：$DECRYPT_URL"

step "自检完成"
echo "下一步：按 Linux/服务器部署清单.md 第 6~10 步做 systemd + Nginx + 验证。"
echo "关键提醒：切 systemd 前先停旧 nohup 网页进程，避免端口静默 +1。"
