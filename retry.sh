#!/usr/bin/env bash
# 循环调用 launch.sh，直到抢到机器。
# 容量不足 -> 等待后重试；被限流 -> 延长等待；连续多次未知错误 -> 停止并通知。
# 用法: ./retry.sh
set -u
# shellcheck source=common.sh
. "$(dirname "$0")/common.sh"
load_config

# 防止同时运行两份
LOCK="${TMPDIR:-/tmp}/oci-a1-retry.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  die "已有另一个 retry.sh 在运行 (锁目录 $LOCK)。确认没有后可执行: rmdir $LOCK"
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT
trap 'log "收到中断信号，退出"; exit 130' INT TERM

attempt=0
unknown=0

log "开始重试: 间隔约 ${INTERVAL_SEC}s (+随机 1~${JITTER_SEC}s)，最多 ${MAX_ATTEMPTS} 次 (0=不限)"

while :; do
  attempt=$((attempt + 1))
  "$DIR/launch.sh"
  rc=$?

  case "$rc" in
    0)
      log "完成，共尝试 $attempt 次"
      exit 0
      ;;
    10)
      unknown=0
      wait_sec=$INTERVAL_SEC
      log "第 $attempt 次: 容量不足"
      ;;
    11)
      unknown=0
      wait_sec=$((INTERVAL_SEC * 4))
      log "第 $attempt 次: 被限流，延长等待"
      ;;
    *)
      unknown=$((unknown + 1))
      wait_sec=$INTERVAL_SEC
      log "第 $attempt 次: 未知错误 (连续 $unknown/$MAX_UNKNOWN_ERRORS)"
      if [ "$unknown" -ge "$MAX_UNKNOWN_ERRORS" ]; then
        "$DIR/notify.sh" "OCI 重试脚本因连续错误停止，请检查日志"
        die "连续 $MAX_UNKNOWN_ERRORS 次非容量类错误，已停止。请检查上面的报错"
      fi
      ;;
  esac

  if [ "$MAX_ATTEMPTS" -gt 0 ] && [ "$attempt" -ge "$MAX_ATTEMPTS" ]; then
    "$DIR/notify.sh" "OCI 重试脚本已达到最大尝试次数 $MAX_ATTEMPTS，未抢到"
    die "已达到最大尝试次数 $MAX_ATTEMPTS"
  fi

  sleep_sec=$((wait_sec + RANDOM % JITTER_SEC + 1))
  log "$sleep_sec 秒后重试"
  sleep "$sleep_sec"
done
