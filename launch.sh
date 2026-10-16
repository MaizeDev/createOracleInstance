#!/usr/bin/env bash
# 单次创建尝试。
# 退出码: 0 = 成功(或同名实例已存在)  10 = 容量不足  11 = 被限流  1 = 其他错误
set -u

# 把标准输入固定指向 /dev/null，让所有子进程（oci 是 Python 程序）拿到一个永远有效的 stdin。
# 否则用 nohup 后台运行后再关掉终端窗口，继承自终端的 stdin 会失效，
# oci 启动时就会报 "Bad file descriptor" 并崩溃，而脚本本身还在跑。
exec < /dev/null
# shellcheck source=common.sh
. "$(dirname "$0")/common.sh"
load_config

# 防重复: 已有同名实例就不再创建
if existing="$(find_instance_id)"; then
  log "已存在同名实例 $INSTANCE_NAME，不再重复创建"
  report_instance "$existing"
  exit 0
fi

log "尝试创建 $INSTANCE_NAME (${OCPUS} OCPU / ${MEMORY_GB}GB, 启动卷 ${BOOT_VOLUME_GB}GB)"

out="$(oci compute instance launch \
  --compartment-id "$COMPARTMENT_ID" \
  --availability-domain "$AVAILABILITY_DOMAIN" \
  --shape VM.Standard.A1.Flex \
  --shape-config "{\"ocpus\": ${OCPUS}, \"memoryInGBs\": ${MEMORY_GB}}" \
  --image-id "$IMAGE_ID" \
  --subnet-id "$SUBNET_ID" \
  --assign-public-ip true \
  --display-name "$INSTANCE_NAME" \
  --boot-volume-size-in-gbs "$BOOT_VOLUME_GB" \
  --ssh-authorized-keys-file "$SSH_PUBLIC_KEY_FILE" 2>&1)"
rc=$?

if [ "$rc" -eq 0 ]; then
  log "创建请求已被接受"
  iid="$(find_instance_id)" || iid=""
  if [ -n "$iid" ]; then
    report_instance "$iid"
  else
    log "已创建，但暂时查不到 OCID，请到控制台确认"
  fi
  exit 0
fi

case "$out" in
  *"Out of host capacity"*|*"Out of capacity"*) exit 10 ;;
  *TooManyRequests*|*'"status": 429'*) exit 11 ;;
esac

log "遇到非容量类错误:"
printf '%s\n' "$out" >&2
exit 1
