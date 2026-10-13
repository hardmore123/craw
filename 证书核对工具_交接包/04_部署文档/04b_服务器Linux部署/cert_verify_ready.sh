#!/usr/bin/env bash
# certverify 开机依赖就绪探测（ExecStartPre）
#
# 断电重启场景：网络/解密服务/代理不会和本服务同时就绪。
# 这里按顺序等待，每个依赖都有上限；超时只告警、不阻塞启动——
# 网页本体缺解密服务仍可运行（只是加密上传会走程序内兜底报错），
# 不应因依赖未就绪让 systemctl 判定服务启动失败而反复 failed。
#
# 用法：由 certverify.service 的 ExecStartPre 调用；也可手工跑调。

set -u

PROJ=/home/hisense/pro/Gitlab/DataGather
DECRYPT_URL="http://10.18.87.51:8500/"
LOG=/tmp/cert_verify_ready.log

log() { printf '%s %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOG"; }

# ---- 1. 等路由/网络就绪（最多 60s）----
ROUTE_OK=""
for i in $(seq 1 30); do
    if ip route get 10.18.87.51 >/dev/null 2>&1 || ping -c1 -W1 10.18.87.51 >/dev/null 2>&1; then
        ROUTE_OK=1; break
    fi
    sleep 2
done
[ -n "${ROUTE_OK:-}" ] && log "[ok] 网络/路由就绪" || log "[warn] 60s 内网络/路由未就绪（继续尝试启动）"

# 2. 等解密服务 :8500 就绪（最多 60s；起不来仅提示，不阻塞）
DECRYPT_OK=""
for i in $(seq 1 60); do
    if curl -s -o /dev/null --max-time 3 "$DECRYPT_URL"; then
        DECRYPT_OK=1; break
    fi
    sleep 1
done
[ -n "${DECRYPT_OK:-}" ] && log "解密服务 8500 就绪" || log "[warn] 解密服务 8500 未就绪（加密上传会报错，Web 本体不影响）"

# 3. 等代理端口就绪（最多 60s；缺代理时 VDE/UL 会查不到，但不阻塞）
PROXY_ADDR=""
if [ -f "$PROJ/.env" ]; then
    # .env 可能是 Windows CRLF，行尾的 \r 必须剥掉，否则主机名带 \r 导致 TCP 探测失败
    PROXY_ADDR=$(grep -E '^CERT_PROXY=http' "$PROJ/.env" | head -1 | sed 's/^CERT_PROXY=//' | tr -d '"\r' | xargs)
fi
if [ -n "$PROXY_ADDR" ]; then
    HOST_PORT="${PROXY_ADDR#*://}"
    HOST="${HOST_PORT%%:*}"
    PORT="${HOST_PORT##*:}"
    for i in $(seq 1 60); do
        bash -c "exec 3<>/dev/tcp/$HOST/$PORT" >/dev/null 2>&1 && { PROXY_OK=1; break; }
        sleep 1
    done
    [ -n "${PROXY_OK:-}" ] && log "代理 $PROXY_ADDR 就绪" || log "[warn] 代理 $PROXY_ADDR 未就绪（VDE/UL 查询可能失败）"
else
    log "未配置 CERT_PROXY，跳过代理探测"
fi

# 4. 给主进程留一点缓冲
sleep 2

# 关键：始终退出 0。依赖缺失 = 可用但降级，绝不能把启动判失败导致 StartLimit 循环。
exit 0