# oci-a1-retry

自动重试创建 Oracle Cloud 的 Always Free Arm 实例（VM.Standard.A1.Flex），直到抢到为止。
只依赖官方 OCI CLI 和 bash，不需要任何第三方服务。

## 文件说明

| 文件 | 作用 |
| --- | --- |
| `config.env.example` | 配置模板，复制为 `config.env` 后填写 |
| `common.sh` | 公共函数：读取并校验配置、查找实例、成功后汇报 |
| `lookup.sh` | 只读查询，帮你找出需要填的 OCID |
| `launch.sh` | 单次创建尝试 |
| `retry.sh` | 循环重试，入口脚本 |
| `notify.sh` | 可选的 Telegram 通知 |

## 使用步骤

### 1. 准备 OCI CLI

- **Cloud Shell**（控制台右上角的终端图标）：自带 CLI 且已认证，直接用。注意先确认控制台右上角区域是新加坡。
  - **本机**：安装 OCI CLI（macOS 可用 `brew install oci-cli`），然后运行 `oci setup config`，按提示生成 API 密钥并把公钥上传到控制台。`~/.oci/config` 里的 `region` 要是 `ap-singapore-1`。

### 2. 生成 SSH 密钥（如果还没有）

```bash
ssh-keygen -t ed25519 -f ~/.ssh/oci_qdii
```

### 3. 填写配置

```bash
cp config.env.example config.env
./lookup.sh          # 查出 COMPARTMENT_ID、SUBNET_ID、IMAGE_ID，填进 config.env
```

### 4. 开始重试

```bash
chmod +x *.sh
./retry.sh
```

后台运行（关掉终端也继续）：

```bash
nohup ./retry.sh > retry.log 2>&1 &
tail -f retry.log
```

macOS 上防止合上盖子后休眠：`caffeinate -i ./retry.sh`。

> Cloud Shell 空闲一段时间会断开，进程也会随之结束，只适合前期试几次。想长时间挂着，请在自己的电脑或常开的机器上运行。

### 5. 抢到之后

脚本会打印公网 IP，直接登录（Ubuntu 镜像的默认用户是 `ubuntu`）：

```bash
ssh -i ~/.ssh/oci_qdii ubuntu@<公网IP>
```

## 安全设计

- **额度闸**：`OCPUS` 超过 2、`MEMORY_GB` 超过 12 时脚本直接拒绝运行，避免超出 Always Free 额度。注意脚本只检查本次配置，**不会统计你账号里已有的 A1 实例**，已有的要自己算进去。
- **防重复**：每次尝试前先查同名实例，已存在就不再创建。
- **防并发**：用锁目录避免同时跑两份。如果进程被强制杀死导致锁残留，执行 `rmdir /tmp/oci-a1-retry.lock`。
- **限流友好**：间隔不能低于 60 秒，带随机抖动，遇到 429 自动拉长等待。
- **遇错即停**：连续 `MAX_UNKNOWN_ERRORS` 次非容量类错误（认证失败、参数错误、额度不足等）就停止，不会无脑循环。
- **密钥不入库**：`config.env`、`*.key`、`*.pem` 已写进 `.gitignore`。私钥只在你本机，脚本只读取公钥文件。

## 常见问题

- **一直提示 Out of host capacity**：正常现象，A1 容量紧张。保持运行即可；升级成 Pay As You Go 后优先级通常更高，不超免费额度就不收费。
- **想要 2 OCPU / 12GB**：直接抢大规格更难。更稳妥的做法是先抢 1 OCPU / 6GB，再用不同的 `INSTANCE_NAME` 抢第二台 1 OCPU / 6GB，合计刚好 2 OCPU / 12GB。在已有实例上原地改大也可能遇到容量问题。
- **报 NotAuthenticated / 401**：检查 `~/.oci/config` 和 API 密钥是否配置正确。
- **创建成功但连不上**：检查 `qdii-vcn` 里是否有 Internet Gateway，路由表是否有 `0.0.0.0/0` 指向它，Security List 是否放行 22 端口。
