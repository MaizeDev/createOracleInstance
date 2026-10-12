# oci-a1-retry

自动重试创建 Oracle Cloud 的 Always Free Arm 实例（`VM.Standard.A1.Flex`），直到抢到为止，抢到后可通过 Telegram 通知。

只依赖官方 OCI CLI 和 bash，不需要任何第三方服务，不接触你的账号密码。

---

## 目录

1. [免费额度速查](#1-免费额度速查)
2. [文件说明](#2-文件说明)
3. [快速开始](#3-快速开始)
4. [详细步骤](#4-详细步骤)
5. [抢到之后](#5-抢到之后)
6. [脚本行为说明](#6-脚本行为说明)
7. [常见问题与排错](#7-常见问题与排错)
8. [安全注意事项](#8-安全注意事项)

---

## 1. 免费额度速查

以下为撰写时的情况，Oracle 的免费政策会调整，**以官方文档为准**。

| 资源 | 免费额度 | 备注 |
| --- | --- | --- |
| Arm (A1.Flex) | 合计 **2 OCPU / 12GB** | 可建 1 台或 2 台，怎么拆都行（1 台 2/12，或 2 台各 1/6）。早年的 4 OCPU / 24GB 已不适用，网上旧教程别照抄 |
| AMD Micro | **2 台** | 每台 1/8 OCPU、1GB 内存，性能很弱 |
| 存储 | 合计 **200GB** | 所有启动卷和块存储加在一起算，每台启动卷最小 47GB |

要点：

- **ARM 和 AMD 是两份独立额度**，互不占用，免费上限是 2 台 ARM + 2 台 AMD，共 4 台。
- **存储容易超**：2 台 ARM（各 50GB）加 2 台 AMD（各 47GB）共 194GB，已接近 200GB 上限，别随手调大启动卷或再加块存储。
- **不会到期**：Always Free 资源只要账号保持活跃就一直免费。30 天试用金到期不影响它们。
- **超额会出问题**：超出免费额度的 A1 实例在试用期结束后会被停用，一段时间后被删除。所以一开始就别建超规格的。
- **别长期闲置**：Oracle 可能回收长期几乎没有负载的 Always Free 实例，具体阈值请查官方文档。
- **Home Region 注册后不能改**，免费资源只能建在 Home Region。在其他区域（比如圣何塞）开机器不属于免费额度。
- **新加坡只有一个可用域（AD-1）**，没法通过换 AD 来绕开容量问题。

---

## 2. 文件说明

| 文件 | 作用 |
| --- | --- |
| `config.env.example` | 配置模板，复制为 `config.env` 后填写 |
| `common.sh` | 公共函数：读取并校验配置、查找实例、成功后汇报（被其他脚本 source，不要单独运行） |
| `lookup.sh` | 只读查询，帮你找出需要填的 OCID，不创建任何资源 |
| `launch.sh` | 单次创建尝试，用来试跑和被 `retry.sh` 调用 |
| `retry.sh` | 循环重试，**日常入口** |
| `notify.sh` | 可选的 Telegram 通知 |
| `.gitignore` | 排除 `config.env`、`*.key`、`*.pem`、`*.log`，防止私密信息进仓库 |

---

## 3. 快速开始

已经熟悉 OCI 的话，按这 6 步走，每步的细节见下一节。

```bash
# 1. 安装并配置 OCI CLI，上传 API 公钥到控制台，然后验证
oci iam region list

# 2. 生成 SSH 密钥
ssh-keygen -t ed25519 -f ~/.ssh/oci_qdii

# 3. 生成配置并查出需要的 ID
chmod +x *.sh
cp config.env.example config.env
./lookup.sh            # 把输出的 ID 填进 config.env

# 4. 单次试跑（exit=10 说明参数都对，只是没容量）
./launch.sh; echo "exit=$?"

# 5. 开始循环重试
nohup ./retry.sh > retry.log 2>&1 &
tail -f retry.log

# 6. 抢到后登录
ssh -i ~/.ssh/oci_qdii ubuntu@<公网IP>
```

---

## 4. 详细步骤

### 4.1 安装并配置 OCI CLI

- **Cloud Shell**（控制台右上角的终端图标）：自带 CLI 且已认证，不用配置。但 Cloud Shell 空闲一段时间会断开，进程随之结束，只适合前期试几次。先确认控制台右上角的区域是你的 Home Region。
- **本机（推荐长时间挂着）**：macOS 用 `brew install oci-cli`，然后运行 `oci setup config`。

`oci setup config` 的提示和填法：

| 提示 | 填什么 |
| --- | --- |
| Enter a location for your config | 回车，用默认的 `~/.oci/config` |
| Enter a user OCID | 控制台右上角头像 → **My profile**，复制 OCID |
| Enter a tenancy OCID | 同一页面的 Tenancy 信息里复制 OCID |
| Enter a region by index or name | 输入区域名或序号。新加坡是 `ap-singapore-1`（列表里的序号是 18）。**注意别选成 `ap-singapore-2`，那是另一个区域** |
| Generate a new API Signing RSA key pair? | `Y` |
| Enter a directory for your keys | 回车，默认 `~/.oci` |
| Enter a name for your key | 回车，默认 `oci_api_key` |
| Enter a passphrase for your private key | 输入 **`N/A`** 表示不设密码。**直接回车不行**，它会反复提示 |

关于密码：脚本要无人值守地运行，私钥不能设密码。如果两次输入的密码不一致，会提示 `The two entered values do not match` 并让你重输，这只是输错了，不是故障。配置完成后可以检查私钥是否被加密：

```bash
head -1 ~/.oci/oci_api_key.pem
```

显示 `-----BEGIN PRIVATE KEY-----` 或 `-----BEGIN RSA PRIVATE KEY-----` 是正常的；带 `ENCRYPTED` 说明设了密码，需要重新生成。

**必须做：把公钥上传到控制台**，否则所有命令都会报 401。

1. `cat ~/.oci/oci_api_key_public.pem`，复制全部内容（包括 BEGIN 和 END 两行）。
2. 控制台头像 → **My profile** → 左侧 **API keys** → **Add API key** → **Paste a public key**，粘贴后点 Add。
3. 弹出的配置预览里的 fingerprint 应该和终端里 `oci setup config` 显示的一致。

验证：

```bash
oci iam region list
```

返回区域列表就说明配好了。如果提示文件权限太宽松：

```bash
oci setup repair-file-permissions --file ~/.oci/config
oci setup repair-file-permissions --file ~/.oci/oci_api_key.pem
```

### 4.2 生成 SSH 密钥

用于登录服务器，和上面的 API 密钥是两回事。

```bash
ssh-keygen -t ed25519 -f ~/.ssh/oci_qdii
```

passphrase 可以回车不设，也可以设一个（只影响你自己登录，不影响脚本，因为脚本只读公钥）。生成后：

- `~/.ssh/oci_qdii`：**私钥**，留在本机，不要发给任何人，不要提交到仓库。
- `~/.ssh/oci_qdii.pub`：公钥，脚本会把它写进服务器。

> 在控制台创建页面里自动生成并下载的那把私钥不需要了，脚本用的是你自己生成的这一对。自己生成的好处是不管重试多少次，用的都是同一把钥匙。

### 4.3 填写配置

```bash
chmod +x *.sh
cp config.env.example config.env
./lookup.sh
```

`lookup.sh` 会依次列出：

- `COMPARTMENT_ID`：根区间，等于你的租户 OCID。
- `AVAILABILITY_DOMAIN`：可用域名称，新加坡是 `uMAA:AP-SINGAPORE-1-AD-1`（`uMAA` 前缀因账号而异，以输出为准）。
- `SUBNET_ID`：在表格里找你要用的子网那一行，复制 OCID。
- `IMAGE_ID`：Ubuntu aarch64 镜像，最新的排在最前面，取第一个。要换版本用 `OS_VERSION=22.04 ./lookup.sh`。

> 输出里可能夹杂 `SyntaxWarning` 和 `This operation supports pagination` 的提示，前者是 OCI CLI 自己的 Python 警告，后者是因为只列了 3 个镜像，**都可以忽略**。

把这些 ID 填进 `config.env`。所有配置项：

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `COMPARTMENT_ID` | 无 | 必填 |
| `AVAILABILITY_DOMAIN` | 无 | 必填 |
| `SUBNET_ID` | 无 | 必填 |
| `IMAGE_ID` | 无 | 必填 |
| `SSH_PUBLIC_KEY_FILE` | 无 | 必填，写成 `$HOME/.ssh/oci_qdii.pub`（不要用 `~`，引号里不会被展开） |
| `INSTANCE_NAME` | `qdii-arm` | 实例名，也用于防重复 |
| `OCPUS` | `1` | 1~2 |
| `MEMORY_GB` | `6` | 1~12 |
| `BOOT_VOLUME_GB` | `50` | 47~200 |
| `INTERVAL_SEC` | `90` | 两次尝试间隔，最低 60 |
| `JITTER_SEC` | `30` | 随机抖动 1~N 秒 |
| `MAX_ATTEMPTS` | `0` | 最大尝试次数，0 为不限 |
| `MAX_UNKNOWN_ERRORS` | `5` | 连续几次非容量类错误后停止 |
| `TELEGRAM_BOT_TOKEN` | 空 | 可选 |
| `TELEGRAM_CHAT_ID` | 空 | 可选 |

默认抢 1 OCPU / 6GB，比一上来要 2/12 更容易成功。

### 4.4 单次试跑

正式循环之前先试一次，确认参数、权限都没问题：

```bash
./launch.sh; echo "exit=$?"
```

| 退出码 | 含义 | 下一步 |
| --- | --- | --- |
| `0` | 创建成功，或同名实例已存在 | 直接看输出里的公网 IP |
| `10` | 容量不足 | **参数全部正确**，可以开始循环重试 |
| `11` | 被限流 | 隔几分钟再试 |
| `1` | 其他错误（认证、参数、额度等） | 看打印出来的报错原文排查，见[常见问题](#7-常见问题与排错) |

### 4.5 开始循环重试

```bash
nohup ./retry.sh > retry.log 2>&1 &
tail -f retry.log
```

- `tail -f` 只是看日志，`Ctrl+C` 退出查看不会停掉脚本。
- 日志里每隔 90~120 秒出现一行"容量不足"是正常的。
- 停掉脚本：`pkill -f retry.sh`。
- 想前台运行且防止 macOS 休眠：`caffeinate -i ./retry.sh`（关掉终端窗口脚本就停了）。

**Mac 休眠时脚本也会停。** 想长时间挂着，请保持插电不合盖，或使用 `caffeinate`，或者放到一台常开的机器上跑。

抢不到的预期：新加坡只有一个 AD，A1 紧张时可能要等几小时甚至几天。想提高概率，可以把账号升级成 Pay As You Go，优先级通常更高。只要用量不超过免费额度就不收费，升级后建议在 Budgets 里设预算告警。

### 4.6 配置 Telegram 通知（可选）

在 `config.env` 里填：

```bash
TELEGRAM_BOT_TOKEN="123456:ABC-你的token"
TELEGRAM_CHAT_ID="你的数字ID"
```

获取方式：

1. 在 Telegram 里找 @BotFather，发 `/newbot` 建一个机器人，得到 token（建议专门建一个用于通知的机器人）。
2. 给这个机器人发一条消息（比如 `/start`），否则它无法主动给你发消息。
3. chat id：私聊的 chat id 就是你的 Telegram 用户 ID，纯数字，不是 @用户名。有的客户端能在个人资料里直接看到；看不到就打开 `https://api.telegram.org/bot<token>/getUpdates`，在返回的 JSON 里找 `"chat":{"id":...}`。

**手动测试要先加载配置**：

```bash
set -a; . ./config.env; set +a
./notify.sh "测试通知"
```

原因：`notify.sh` 自己不读 `config.env`，只看当前环境里有没有那两个变量。`retry.sh` 和 `launch.sh` 启动时会自动加载配置，正式运行不用管；但手动单独运行 `notify.sh` 时不先加载就会静默跳过，看起来像没反应。这个加载只对当前终端窗口有效。

会推送的情况：抢到机器、连续多次非容量类错误导致停止、达到 `MAX_ATTEMPTS` 上限。普通的"容量不足"不推送。发送失败不会影响重试，所以**首次务必手动测一遍**。

---

## 5. 抢到之后

脚本会打印公网 IP 和登录命令，自动退出（配了 Telegram 也会推送）。登录：

```bash
ssh -i ~/.ssh/oci_qdii ubuntu@<公网IP>
```

Ubuntu 镜像的默认用户是 `ubuntu`。

### 5.1 如果连不上：检查网络

创建成功但 SSH 连不上，通常是网络没配通。需要满足：

1. VCN 里有一个状态为 Available 的 **Internet Gateway**。
2. 子网使用的**路由表**里有 `0.0.0.0/0` 指向这个 Internet Gateway。
3. 子网的 **Security List** 入站规则放行 TCP 22。

用 CLI 一次查完（把三个占位符换成你自己的 OCID）：

```bash
T=<租户OCID>
V=<VCN的OCID>
S=<子网的OCID>

echo "== 1. Internet Gateway =="
oci network internet-gateway list -c $T --vcn-id $V \
  --query 'data[].{name:"display-name",state:"lifecycle-state",enabled:"is-enabled"}' --output table

echo "== 2. 子网使用的路由表 ID =="
oci network subnet get --subnet-id $S --query 'data."route-table-id"' --raw-output

echo "== 3. 路由规则 =="
oci network route-table list -c $T --vcn-id $V \
  --query 'data[].{name:"display-name",id:id,rules:"route-rules"}' --output json

echo "== 4. 安全列表入站规则 =="
oci network security-list list -c $T --vcn-id $V \
  --query 'data[].{name:"display-name",ingress:"ingress-security-rules"}' --output json
```

判断标准：第 1 项有一行且 `enabled` 为 `True`、`state` 为 `AVAILABLE`；第 3 项里第 2 项那张路由表有 `0.0.0.0/0` 指向该网关；第 4 项里有 `0.0.0.0/0`、TCP、端口范围含 22 的规则。如果 VCN 是用"带互联网连接的向导"建的，这些通常已经配好。

### 5.2 以后开放其他端口（如 80/443）

要在**两处**放行：Security List（或 NSG）里加入站规则，以及 Ubuntu 系统自带的 iptables（Oracle 的镜像默认会拦截）。只改其中一处不会生效。

### 5.3 抢第二台

在 `config.env` 里把 `INSTANCE_NAME` 改成另一个名字（如 `qdii-arm-2`），再运行一次 `./retry.sh`。两台 ARM 合计不能超过 2 OCPU / 12GB。

注意同一时间只能运行一份 `retry.sh`（有锁），第二台要等第一台抢到后再抢。

---

## 6. 脚本行为说明

### 重试策略

| 情况 | 行为 |
| --- | --- |
| 容量不足（Out of host capacity） | 等 `INTERVAL_SEC` + 随机 1~`JITTER_SEC` 秒后重试 |
| 被限流（429 / TooManyRequests） | 等待时间延长为 4 倍 |
| 其他错误 | 计数，连续 `MAX_UNKNOWN_ERRORS` 次后停止并通知；中间遇到容量不足或限流会清零计数 |
| 成功 | 等待实例进入 RUNNING，打印公网 IP，退出 |

### 安全设计

- **额度闸**：`OCPUS` 超过 2、`MEMORY_GB` 超过 12、`BOOT_VOLUME_GB` 超出 47~200、`INTERVAL_SEC` 低于 60 时脚本直接拒绝运行。注意它**只检查本次配置，不会统计账号里已有的 A1 实例**，已有的要自己算进去。
- **防重复**：每次尝试前先按 `INSTANCE_NAME` 查同名且未销毁的实例，存在就不再创建。想再开一台，必须改名字。
- **防并发**：用锁目录避免同时跑两份。
- **遇错即停**：认证失败、参数错误等不会被无限重试。
- **密钥不入库**：`.gitignore` 排除了 `config.env`、`*.key`、`*.pem`、`*.log`。脚本只读取公钥文件。

---

## 7. 常见问题与排错

| 现象 | 原因与处理 |
| --- | --- |
| `Out of host capacity` / `exit=10` | 正常现象，A1 容量紧张。保持 `retry.sh` 运行；或升级 Pay As You Go 提高优先级 |
| `NotAuthenticated` / 401 | 检查公钥是否已上传到控制台、fingerprint 是否一致、`~/.oci/config` 是否正确 |
| `oci setup config` 里密码反复提示 | 不设密码要输入 `N/A`，不能直接回车 |
| 提示 `do not match` | 两次输入的密码不一致，重新输入即可 |
| 提示文件权限过宽 | 执行 `oci setup repair-file-permissions --file <文件>`（见 4.1） |
| 输出里有 `SyntaxWarning` | OCI CLI 自己的 Python 警告，忽略 |
| 输出里有 `supports pagination` | 因为只列了几条结果，忽略 |
| `LimitExceeded` | 额度已用完，检查账号里是否已有 A1 实例，或区域/额度限制 |
| 提示"已有另一个 retry.sh 在运行" | 先确认真的没有在跑（`pgrep -f retry.sh`），再删除锁目录：`rmdir /tmp/oci-a1-retry.lock`。macOS 上锁可能在 `$TMPDIR` 下，用 `ls $TMPDIR \| grep oci` 查找 |
| 脚本跑一阵就不动了 | Mac 可能休眠了；Cloud Shell 可能空闲断开了 |
| 创建成功但 SSH 连不上 | 见 [5.1](#51-如果连不上检查网络) |
| 想要 2 OCPU / 12GB | 直接抢大规格更难。更稳妥：先抢 1/6，再改名抢第二台 1/6。在已有实例上原地改大也可能遇到容量问题 |
| 想要免费的圣何塞机器 | 不可行。免费资源只能建在 Home Region，Home Region 不能改 |
| 改完 `config.env` 没生效 | `retry.sh` 启动时读取配置，修改后需要 `pkill -f retry.sh` 再重新启动 |

---

## 8. 安全注意事项

- **私钥**（`~/.oci/oci_api_key.pem`、`~/.ssh/oci_qdii`）只留在本机，不要提交到仓库、不要发给任何人。
- **`config.env`** 含 Telegram token 等信息，已被 `.gitignore` 排除，提交前用 `git status` 确认它没有被加入。
- **OCID** 本身不是密钥，别人凭它登不进账号，但也不建议公开。分享日志或截图前可以先打码。
- 频率不要调得太激进，脚本强制间隔不低于 60 秒，是为了避免被限流。
