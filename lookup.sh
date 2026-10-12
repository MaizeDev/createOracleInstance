#!/usr/bin/env bash
# 只读查询: 帮你找出 config.env 里需要填的 ID，不会创建任何资源。
# 用法: ./lookup.sh            (默认 Ubuntu 24.04)
#       OS_VERSION=22.04 ./lookup.sh
set -u
# shellcheck source=common.sh
. "$(dirname "$0")/common.sh"
need_oci

tenancy="${OCI_TENANCY:-}"
if [ -z "$tenancy" ] && [ -f "$HOME/.oci/config" ]; then
  tenancy="$(awk -F= '/^[[:space:]]*tenancy[[:space:]]*=/{gsub(/[[:space:]]/,"",$2); print $2; exit}' "$HOME/.oci/config")"
fi
[ -n "$tenancy" ] || die "读不到租户 OCID，请确认 oci 已配置 (oci setup config)"

echo
echo "== COMPARTMENT_ID (根区间 = 租户 OCID) =="
echo "$tenancy"

echo
echo "== AVAILABILITY_DOMAIN =="
oci iam availability-domain list --compartment-id "$tenancy" \
  --query 'data[].{name:name}' --output table

echo
echo "== SUBNET_ID (找 qdii-public-subnet 那一行) =="
oci network subnet list --compartment-id "$tenancy" \
  --query 'data[].{name:"display-name",id:id}' --output table

echo
echo "== IMAGE_ID (Ubuntu ${OS_VERSION:-24.04} aarch64，最新 3 个) =="
oci compute image list --compartment-id "$tenancy" \
  --operating-system "Canonical Ubuntu" \
  --operating-system-version "${OS_VERSION:-24.04}" \
  --shape VM.Standard.A1.Flex \
  --sort-by TIMECREATED --sort-order DESC --limit 3 \
  --query 'data[].{name:"display-name",id:id}' --output table
