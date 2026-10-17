#!/usr/bin/env bash
# 公共函数: 日志、读取并校验配置、查找实例、成功后汇报。由其他脚本 source，不要单独运行。

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${CONFIG_FILE:-$DIR/config.env}"

log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*"; }

die() { log "错误: $*"; exit 1; }

is_int() { case "$1" in ''|*[!0-9]*) return 1 ;; *) return 0 ;; esac; }

need_oci() {
  command -v oci >/dev/null 2>&1 || die "找不到 oci 命令。Cloud Shell 自带；本机请先安装 OCI CLI 并运行 oci setup config"
}

# 只读取 config.env，不检查 oci、不校验抢机参数。Telegram 遥控只需要 token 和用户 ID，
# 不该因为抢机配置有问题就起不来(那正是你最需要远程排查的时候)。
load_env() {
  [ -f "$CONFIG_FILE" ] || die "找不到 ${CONFIG_FILE}，请先 cp config.env.example config.env 并填写"

  set -a
  # shellcheck source=/dev/null
  . "$CONFIG_FILE"
  set +a
}

load_config() {
  need_oci
  load_env

  : "${COMPARTMENT_ID:?请在 config.env 填写 COMPARTMENT_ID}"
  : "${AVAILABILITY_DOMAIN:?请在 config.env 填写 AVAILABILITY_DOMAIN}"
  : "${SUBNET_ID:?请在 config.env 填写 SUBNET_ID}"
  : "${IMAGE_ID:?请在 config.env 填写 IMAGE_ID}"
  : "${SSH_PUBLIC_KEY_FILE:?请在 config.env 填写 SSH_PUBLIC_KEY_FILE}"
  : "${INSTANCE_NAME:=qdii-arm}"
  : "${OCPUS:=1}"
  : "${MEMORY_GB:=6}"
  : "${BOOT_VOLUME_GB:=50}"
  : "${INTERVAL_SEC:=90}"
  : "${JITTER_SEC:=30}"
  : "${MAX_ATTEMPTS:=0}"
  : "${MAX_UNKNOWN_ERRORS:=5}"

  local v
  for v in OCPUS MEMORY_GB BOOT_VOLUME_GB INTERVAL_SEC JITTER_SEC MAX_ATTEMPTS MAX_UNKNOWN_ERRORS; do
    is_int "${!v}" || die "$v 必须是整数，当前值: ${!v}"
  done

  # 安全闸: 绝不超出 Always Free 额度，避免被收费或被停机
  [ "$OCPUS" -ge 1 ] && [ "$OCPUS" -le 2 ] || die "OCPUS 必须在 1~2 之间 (Always Free 上限 2)"
  [ "$MEMORY_GB" -ge 1 ] && [ "$MEMORY_GB" -le 12 ] || die "MEMORY_GB 必须在 1~12 之间 (Always Free 上限 12)"
  [ "$BOOT_VOLUME_GB" -ge 47 ] && [ "$BOOT_VOLUME_GB" -le 200 ] || die "BOOT_VOLUME_GB 必须在 47~200 之间"
  [ "$INTERVAL_SEC" -ge 60 ] || die "INTERVAL_SEC 不能低于 60，频率过高会被限流"
  [ "$JITTER_SEC" -ge 1 ] || die "JITTER_SEC 至少为 1"
  [ -f "$SSH_PUBLIC_KEY_FILE" ] || die "找不到公钥文件: $SSH_PUBLIC_KEY_FILE"
}

# 查找同名且未被销毁的实例，找到则输出 OCID，找不到返回 1。用于防止重复创建。
find_instance_id() {
  local id
  id="$(oci compute instance list \
    --compartment-id "$COMPARTMENT_ID" \
    --display-name "$INSTANCE_NAME" \
    --query "data[?\"lifecycle-state\"!='TERMINATED' && \"lifecycle-state\"!='TERMINATING'].id | [0]" \
    --raw-output 2>/dev/null)"
  case "$id" in ''|null) return 1 ;; esac
  printf '%s' "$id"
}

# 成功后: 等待 RUNNING，打印公网 IP 和登录命令，并发送通知
report_instance() {
  local iid="$1" ip
  log "等待实例进入 RUNNING (最多 10 分钟)..."
  oci compute instance get --instance-id "$iid" \
    --wait-for-state RUNNING --max-wait-seconds 600 >/dev/null 2>&1
  ip="$(oci compute instance list-vnics --instance-id "$iid" \
    --query 'data[0]."public-ip"' --raw-output 2>/dev/null)"
  [ "$ip" = "null" ] && ip=""
  log "实例 OCID: $iid"
  log "公网 IP: ${ip:-(暂未获取，请稍后在控制台查看)}"
  log "登录命令: ssh -i <你的私钥> ubuntu@${ip:-<IP>}"
  "$DIR/notify.sh" "OCI Arm 实例已创建: ${INSTANCE_NAME}，公网 IP: ${ip:-稍后在控制台查看}"
}
