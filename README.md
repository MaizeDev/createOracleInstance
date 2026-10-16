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
7. [运行中遇到的问题（实战记录）](#7-运行中遇到的问题实战记录)
8. [常见问题与排错](#8-常见问题与排错)
9. [安全注意事项](#9-安全注意事项)

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
| `ctl.sh` | **日常入口**：一键启动 / 停止 / 重启 / 查看状态，可在任意目录运行 |
| `ctl.py` | `ctl.sh` 的实际实现 |
| `retry.sh` | 循环重试本体，一般不需要直接运行，由 `ctl.sh` 启动 |
| `notify.sh` | 可选的 Telegram 通知 |
| `tg_control.sh` | 可选的 Telegram 遥控入口：加载配置后启动 `tg_control.py` |
| `tg_control.py` | 遥控主程序：校验发送者身份、只响应 3 条固定命令 |
| `runner.py` | 启动 / 停止 / 查询两个后台服务的逻辑，`ctl.sh` 和 Telegram 机器人共用 |
| `procs.py` | 进程查找与终止，只会识别和终止位于本项目目录的脚本 |
| `tg_api.py` | Telegram Bot API 的最小封装（只用标准库） |
| `.gitignore` | 排除 `config.env`、`*.key`、`*.pem`、`*.log`、`*.pid`，防止私密信息进仓库 |

---

## 3. 快速开始

首次配置（只做一次）：

```bash
oci iam region list
ssh-keygen -t ed25519 -f ~/.ssh/oci_qdii
chmod +x *.sh
cp config.env.example config.env
./lookup.sh
./launch.sh; echo "exit=$?"
```

日常使用：

```bash
./ctl.sh up
./ctl.sh status
./ctl.sh log
./ctl.sh down
./ctl.sh restart
```

设置短命令 `coi`（可选，之后在任意目录都能用；这个项目目录本身就叫 `coi`，正好同名）：

```bash
# 在项目目录里执行这一行即可，$PWD 会展开成项目路径
echo "coi() { '$PWD/ctl.sh' \"\$@\"; }" >> ~/.zshrc && source ~/.zshrc
```

```bash
coi up          # 一键启动：抢机脚本 + Telegram 遥控
coi bot-start   # 只启动遥控，不动抢机脚本（抢机已经在跑时用这条）
coi status      # 查看两者状态
coi log         # 实时看抢机日志
coi down        # 两个都停
coi -h          # 显示全部命令和说明（忘了命令名就看这个）
```

只想让手机能遥控、不想重启抢机脚本时，用 `coi bot-start`：它只拉起遥控进程，"已经在跑的抢机脚本"不会被打断。

抢到后登录：

```bash
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
| `TELEGRAM_CHAT_ID` | 空 | 可选；使用遥控功能时必须是你自己的数字用户 ID |
| `TG_CMD_MAX_AGE_SEC` | `120` | 可选，遥控命令的有效期，超过的旧消息被忽略 |

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
| `1` | 其他错误（认证、参数、额度等） | 看打印出来的报错原文排查，见[常见问题](#8-常见问题与排错) |

### 4.5 开始循环重试

```bash
./ctl.sh up
```

**`ctl.sh` 命令一览**（设置了 `coi` 短命令后，把 `./ctl.sh` 换成 `coi` 即可，例如 `coi up`）：

| 命令 | 作用 |
| --- | --- |
| `./ctl.sh up` | **一键启动**：抢机脚本 + Telegram 遥控 |
| `./ctl.sh down` | **一键停止**两者 |
| `./ctl.sh restart` | 先停再启 |
| `./ctl.sh status` | 查看两者状态，抢机脚本会附带最近几行日志 |
| `./ctl.sh start` / `stop` | 只启动 / 停止抢机脚本 |
| `./ctl.sh bot-start` / `bot-stop` | 只启动 / 停止 Telegram 遥控 |
| `./ctl.sh log` / `botlog` | 实时查看抢机 / 遥控日志，`Ctrl+C` 退出查看，不影响脚本 |
| `./ctl.sh -h` / `--help` | 显示全部命令和说明（`coi -h`、`coi help` 一样；不带参数也会打印） |

没有配置 Telegram 的话，`up` 会提示遥控启动失败，但抢机脚本照常启动，也可以直接用 `./ctl.sh start`。

**输出长这样**（终端里自动上色：运行中=绿色、未运行=灰色、失败=红色；颜色只在终端出现，管道/重定向里自动关闭，也可以 `coi status --no-color` 或 `NO_COLOR=1` 关掉）：

```text
  ── 抢机脚本 ──────────────────────────────
    运行中 · pid 60042 · 已运行 12 分钟
    最近日志:
      [2026-10-16 20:19:11] 尝试创建 qdii-arm (1 OCPU / 6GB, 启动卷 50GB)

  ── 遥控机器人 ────────────────────────────
    运行中 · pid 59441 · 已运行 13 分钟
```

`up` / `down` / `restart` 则是每个服务一行、标签对齐，失败时把**本次**输出缩进跟在后面：

```text
  ✓ 抢机脚本    已启动
  ✗ 遥控机器人  启动后立即退出 (退出码 1)
      已有另一个 tg_control 在运行 (pid 55971)
```

**`ctl.sh` 替你做了这些事**，以前要手动敲好几行：

- **防休眠**：在 Mac 上自动用 `caffeinate -is` 启动，原因见 [7.1](#71-脚本时断时续mac-休眠)。
- **关终端不受影响**：独立会话运行，标准输入固定为 `/dev/null`，不会再出现 [7.4](#74-关掉终端后-oci-报-bad-file-descriptor连续-5-次后脚本停止) 的崩溃。
- **防重复**：已经在运行就不会再启动第二份。
- **残留的锁自动清理**：不用再手动 `rmdir`。如果发现别的目录里有同名脚本在运行，它宁可拒绝启动也不会乱删锁。
- **一条命令彻底停止**：强制终止，连同正在执行的 `oci` 子进程一起结束，并清理锁。**手动启动的脚本（比如 `nohup ./retry.sh ...`）也能被它识别并停掉。**
- **不会误杀**：只终止经过校验、位于本项目目录的脚本，别的目录里同名的脚本、或者只是用编辑器打开 `retry.sh` 的进程都不会被碰。

**其他要点**

- 日志里每隔 90~120 秒出现一行"容量不足"是正常的。
- **Mac 休眠时脚本会被冻结。** 除了 `caffeinate`，还要保持接通电源、不要合盖；想长时间挂着，可以放到一台常开的机器上跑。
- 电脑重启或关机后，后台进程都会结束，需要重新执行一次 `./ctl.sh up`。
- 想手动启动也可以，命令见 [7.1](#71-脚本时断时续mac-休眠)；停止时用 `./ctl.sh down` 同样有效。

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

### 4.7 Telegram 遥控（可选）

不用回到电脑，直接在 Telegram 里让脚本开始或停止抢机。**只支持 3 条固定命令**：

| 命令 | 作用 |
| --- | --- |
| `/grab_start` | 启动抢机脚本（已在运行则提示，不会起第二份） |
| `/grab_stop` | 强制停止并清理锁 |
| `/grab_status` | 查看是否在运行和最近几行日志（只读，不需要可删掉） |

**前提**

- 已按 4.6 配好 `TELEGRAM_BOT_TOKEN` 和 `TELEGRAM_CHAT_ID`。
- **`TELEGRAM_CHAT_ID` 必须是你自己的数字用户 ID**，它同时也是唯一被授权遥控的用户。不是数字就拒绝启动。
- 电脑上有 `python3`（只用标准库，不用装任何包）。

**启动和停止遥控进程**

```bash
./ctl.sh bot-start
./ctl.sh bot-stop
```

`./ctl.sh up` 会连同抢机脚本一起启动，`./ctl.sh down` 一起停止。手机收到"抢机遥控已上线"说明成功。遥控进程退出**不影响**已经在运行的抢机脚本。

**菜单按钮**

遥控进程启动时会自动注册命令菜单，不需要去 @BotFather 里设置。在和机器人的聊天窗口里点输入框旁边的"菜单"按钮，就能看到三条命令，点一下即可发送。菜单**只注册给你本人的私聊**，别人打开这个机器人看不到这些命令。如果菜单没出现，先看 `./ctl.sh botlog` 里有没有"注册菜单按钮失败"，再退出聊天窗口重新进入。

**手机上收到的回复**

回复是纯文本 + emoji，一眼能看出结果：

```text
/grab_status  →
🟢 抢机脚本 运行中
pid 60042 · 已运行 12 分钟

📄 最近日志
[20:31:09] 尝试创建 qdii-arm（1C/6GB）
[20:32:53] 第 12 次: 被限流，延长等待

/grab_start   →  ✅ 抢机脚本 已启动          （已经在跑则是 ℹ️ 抢机脚本 已经在运行，无需重复启动）
/grab_stop    →  🛑 抢机脚本 已强制停止
出错时         →  ❌ 抢机脚本 启动后立即退出 (退出码 1) + "本次输出:" 后面跟本次日志
```

手机上聊天窗口窄，所以日志行做了三层处理，**保证每条都不折行**：

1. **压缩常见长句**：`尝试创建 qdii-arm (1 OCPU / 6GB, 启动卷 50GB)` → `尝试创建 qdii-arm（1C/6GB）`；`开始重试: 间隔约 90s (+随机 1~30s)，最多 0 次 (0=不限)` → `开始重试: 间隔 90s，不限次数`。只改聊天里的显示，`retry.log` 原文不变（`coi log` 里还是完整内容）。
2. **时间戳只留时分秒**：`[2026-10-16 20:31:07]` → `[20:31:07]`。
3. **兜底截断**：仍然超长的行（比如 OCID、JSON 报错片段）按显示宽度（中文算 2 列）截到 `TG_LOG_WIDTH` 列以内，用 `…` 收尾。

`TG_LOG_WIDTH` 默认 **40** 列；如果你手机上还是折行就调小、想多看点内容就调大，在 `config.env` 里加一行即可（改完 `coi bot-stop && coi bot-start` 生效）：

```bash
TG_LOG_WIDTH=40
```

终端里的 `coi status` 不受这个值影响，它按终端实际宽度渲染，也不会做上面的压缩（宽终端能看到完整日志）。

**原理：为什么需要一个一直在线的遥控进程**

```text
Telegram 服务器  <──(每约 30 秒一次长轮询，由你的电脑主动发起)──  tg_control（常驻，很轻）
                                                                      │ 收到 /grab_start
                                                                      ▼
                                                        启动 retry.sh（只在抢机时运行）
```

- 要让机器人"听得到"命令，电脑上必须有一个进程一直在问 Telegram："有新消息吗？"这就是 `tg_control`。它是**主动向外**发起连接，所以不需要开放端口，也不需要公网 IP。
- `tg_control` 收到命令后，在本地启动或终止 `retry.sh`。这是两个相互独立的程序，**并不冲突**：前者常驻、几乎不占资源，后者只在抢机时运行。
- 因此机器人能**启停抢机脚本**，但**不能启动它自己**：遥控进程不在运行时（电脑休眠、重启、被 `bot-stop` 停掉），Telegram 发什么都没有反应，需要回到电脑上执行 `./ctl.sh up` 或 `bot-start`。

**安全设计**

任何一条不满足，消息都会被直接忽略，不回复、不暴露任何信息：

1. **只认你本人**：必须是私聊，且发送者的数字 ID 等于 `TELEGRAM_CHAT_ID`。只看数字 ID，不看用户名（用户名可以随时改）。群聊、其他人、其他机器人发来的消息一律无效。
2. **只执行固定命令**：消息必须**恰好**是上表中的一条命令，多出任何内容（参数、分号、`&&` 等）都视为无效。消息文本不会被拼进任何命令行，机器人只能启动、停止、查询抢机脚本，没法执行任何其他命令，也不能通过它修改配置。
3. **防重放**：启动时丢弃积压的旧消息，超过 `TG_CMD_MAX_AGE_SEC`（默认 120 秒）的消息也会被忽略，不会因为离线期间堆积的旧命令误操作。
4. **不泄露**：token 不会写进日志，日志也不记录消息内容；回复和菜单都只发给授权用户。
5. **只允许一份**：重复启动遥控进程会被拒绝，避免两个进程同时拉取消息互相冲突。

即使你的 Telegram 账号被他人登录，对方最多也只能执行这 3 条命令。建议给 Telegram 账号开启两步验证。

**给机器人加固（在 @BotFather 里操作）**

- `/setjoingroups` → 选这个机器人 → `Disable`，禁止它被拉进群。
- token 一旦泄露，立刻 `/revoke` 重新生成，并更新 `config.env`，然后 `./ctl.sh bot-stop` 再 `./ctl.sh bot-start`。**不要把 token 贴到聊天或截图里。**

**使用须知**

- **机器人按进程识别抢机脚本**，所以手动启动的脚本它也能识别和停止，只要脚本位于本项目目录。
- **`/grab_stop` 是强制终止（SIGKILL）**，不会等当前这次尝试结束。万一正好有一个创建请求在途中，实例可能已经创建成功；下次启动时 `launch.sh` 会按名字检测到已有实例，不会重复创建。
- **遥控进程本身也要保持运行。** Mac 休眠时它不会响应命令，所以同样要接电、不合盖。想一直在线，可以放到常开的机器上。

**排错**

| 现象 | 原因与处理 |
| --- | --- |
| 发命令没有任何回复 | 先 `./ctl.sh status` 确认遥控在运行，再看 `./ctl.sh botlog`。如果有"忽略未授权消息 (from id=…)"且 id 是你自己的，说明 `TELEGRAM_CHAT_ID` 填错了 |
| 日志里反复出现 `409 Conflict` | 有另一个程序在拉取同一个机器人的消息，比如设置过 webhook，或者在别的机器上也跑着同一个机器人 |
| 启动时报"TELEGRAM_CHAT_ID 必须是…数字" | 填成了用户名或带了其他字符，改成纯数字 |
| `/grab_start` 回复"启动后立即退出 (退出码 N)" | 回复里附带的是**这一次**启动新写入的日志（不再混进历史记录）。退出码 1 且日志提到 `config.env`，通常是配置校验没通过 |
| `/grab_start` 回复"别处有同名脚本在运行" | 另一个目录里有名为 `retry.sh` 的脚本在跑，且存在残留的锁。确认它不是你要的之后停掉它，或手动清理锁目录 |
| `botlog` 里出现"长轮询连接被中断 (RemoteDisconnected / TimeoutError)，属于代理/网络的正常现象" | 本机代理（如 Surge）或网络把长轮询连接掐断了，几秒后会自动接上，**不用处理**。只有长时间收不到回复才需要排查网络/代理 |
| 菜单按钮没出现 | 看 `./ctl.sh botlog` 有没有"注册菜单按钮失败"；退出聊天窗口重新进入；仍不行就重启 Telegram |

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
- **遥控只认你本人、只执行 3 条固定命令**：详见 4.7，消息文本不会进入任何命令行。
- **密钥不入库**：`.gitignore` 排除了 `config.env`、`*.key`、`*.pem`、`*.log`。脚本只读取公钥文件。

---

## 7. 运行中遇到的问题（实战记录）

以下是脚本实际跑起来后遇到的问题，按出现顺序整理。先用速查表定位，再看对应小节。

| 现象 | 可能原因 | 见 |
| --- | --- | --- |
| 两次尝试的间隔远超设定值，或单次尝试"耗时"几十分钟 | Mac 休眠，进程被冻结 | 7.1 |
| 日志出现 `connection timed out` | 网络波动，或刚从休眠唤醒 | 7.2 |
| 日志出现"被限流，延长等待" | 请求过于频繁，脚本自动放慢 | 7.2 |
| 新启动的脚本立刻退出，提示已有另一个在运行 | 旧进程没死，或锁目录残留 | 7.3 |
| `pkill` 之后 `pgrep` 仍能看到旧进程 | 终止信号被推迟处理 | 7.3 |
| 日志出现 `Fatal Python error ... Bad file descriptor` | 关掉终端后标准输入失效 | 7.4 |
| 想修改脚本 | 不能在脚本运行中替换文件 | 7.5 |

### 7.1 脚本时断时续：Mac 休眠

**现象**

脚本设定的间隔是 90~120 秒，但日志里相邻两次尝试相隔 1~3 小时，单次尝试"耗时"十几分钟，偶尔还有 `connection timed out`：

```text
[14:10:56] 98 秒后重试
[15:38:36] 尝试创建 qdii-arm ...        <- 实际隔了约 1.5 小时
[15:55:34] 第 188 次: 容量不足
[15:55:34] 106 秒后重试
[18:41:36] 尝试创建 qdii-arm ...        <- 实际隔了近 3 小时
[19:04:07] 第 189 次: 容量不足          <- 这次尝试"耗时" 22 分钟
```

**原因**

系统休眠。休眠期间进程被冻结，墙上时钟照走，唤醒后脚本接着往下执行，于是日志里就出现了"耗时 22 分钟"的假象。

> **锁屏不是原因。** 锁屏只是屏幕被锁住，后台进程照常运行。很多时候是先休眠、唤醒后因为设了"唤醒需要密码"才看到锁屏界面，锁屏是休眠的结果，不是原因。

**确认方法**

```bash
pmset -g log | grep -E " (Sleep|Wake) +" | tail -20
```

和日志对照。本例中的记录是：

| 时间 | 事件 | 说明 |
| --- | --- | --- |
| 18:41:36 | 脚本开始一次尝试 | 日志 |
| 18:41:51 | Sleep | 原因 Maintenance Sleep，进程在此冻结 |
| 18:58:32 | Wake | 短暂唤醒，随即再次睡眠 |
| 19:04:03 | Wake | 因 Notification 唤醒，脚本继续运行 |
| 19:04:07 | 脚本记录"容量不足" | 日志 |

`DarkWake` 是睡眠期间被动的短暂唤醒（做维护、收通知），不代表脚本在正常运行，别把它当成脚本活着的证据。

**解决**

用 `caffeinate` 启动脚本。`./ctl.sh up` 和 `./ctl.sh start` 已经自动带上了；想手动启动的话，命令是：

```bash
nohup caffeinate -is ./retry.sh < /dev/null >> retry.log 2>&1 &
```

| 参数 | 作用 |
| --- | --- |
| `-i` | 阻止空闲睡眠 |
| `-s` | 接通电源时阻止系统睡眠 |

`caffeinate` 会在 `retry.sh` 退出（抢到机器）后自动结束，不会一直占着。

**必须同时满足：**

- 保持**接通电源**，`-s` 只在接电时生效。
- **不要合盖**，MacBook 合盖会强制睡眠，`caffeinate` 拦不住（接外接显示器和电源的合盖模式除外）。
- 锁屏、屏幕熄灭都没关系。

**验证是否生效**

```bash
pmset -g assertions | grep -i caffeinate
```

应当看到 `caffeinate` 创建的两项断言，且 `Details` 里写着 `caffeinate asserting on behalf of './retry.sh'`：

```text
PreventUserIdleSystemSleep named: "caffeinate command-line tool"
PreventSystemSleep         named: "caffeinate command-line tool"
```

> 只看 `PreventUserIdleSystemSleep` 不够：Handoff、蓝牙、"显示器亮着时的电源管理"等系统进程也会产生这一项，**必须看到 `caffeinate` 那一行**才算数。

**判断是否恢复正常**

- 日志里两次尝试的间隔稳定在 90~120 秒（被限流时约 6 分钟）。
- 单次尝试只花几秒到十几秒。
- 再查一遍 `pmset -g log`，这段时间里没有新的 Sleep 记录。

**长期方案**

笔记本合盖就停，不适合长期挂着。可以放到不休眠的机器上跑，比如先开一台免费的 AMD Micro 实例（不占 ARM 额度，容量通常充足）。代价是那台机器上要安装 OCI CLI 并配置 API 密钥，私钥放在服务器上要严格控制权限；另外 1GB 内存跑这个脚本应该够用，但没有实测过。

### 7.2 日志里出现的几种非容量提示

| 日志内容 | 含义 | 要处理吗 |
| --- | --- | --- |
| `连续 1/5` 的未知错误，报错为 `The connection to endpoint timed out` | 网络波动，或刚从休眠唤醒、网络还没恢复 | 通常不用。下一次遇到"容量不足"或"被限流"会把计数清零，只有**连续 5 次**才会停止 |
| `被限流，延长等待` 后跟一个约 373 秒的等待 | 触发 429，脚本自动把等待拉长到 4 倍 | 不用，这是设计内的行为。**不要因此把间隔调得更小** |
| 频繁出现 timeout | 网络或代理不稳定 | 检查本机网络、代理或 VPN 设置 |

### 7.3 停不掉脚本，新启动的脚本立刻退出

**现象**

- 重新启动 `nohup caffeinate -is ./retry.sh ...` 后马上显示 `exit 1`，`retry.log` 末尾出现：

  ```text
  错误: 已有另一个 retry.sh 在运行 (锁目录 /var/folders/.../T//oci-a1-retry.lock)。确认没有后可执行: rmdir ...
  ```

- 执行 `pkill -f retry.sh` 之后，`pgrep -fl retry.sh` 仍然显示旧进程（如 `bash ./retry.sh`）。

**原因**

1. **旧进程没死。** `retry.sh` 设置了终止信号的处理函数（trap），而 bash 在等待前台命令（`sleep` 或 `oci` 调用）期间会**推迟处理信号**。所以普通的 `pkill`（SIGTERM）要等当前这次等待或调用结束才生效，限流时一次等待就有 6 分钟左右。这是目前脚本的一个已知限制。
2. **锁拦住了新进程。** 脚本用锁目录防止同时运行两份，位置是 `${TMPDIR:-/tmp}/oci-a1-retry.lock`。macOS 上 `$TMPDIR` 是形如 `/var/folders/.../T/` 的路径，**不是 `/tmp`**。旧进程还活着时，新进程必然撞锁退出，`caffeinate` 随之结束，所以 `pmset` 里也看不到它。

> **不要在旧进程还活着时手动删锁。** 否则旧进程处于"无锁"状态，再启动新的就会出现两份同时运行。

**一键处理（推荐）**

```bash
./ctl.sh restart
```

`ctl.sh` 会强制终止旧进程（包括手动启动的），清理残留的锁，再重新启动，并且不会误杀别处的同名脚本。只想停掉不重启就用 `./ctl.sh down`。

**手动处理（`ctl.sh` 用不了时的备选，顺序不能颠倒）**

```bash
pkill -9 -f retry.sh                              # 1. 强制终止
sleep 1
pgrep -fl retry.sh                                # 2. 确认旧进程已消失，应无输出；有输出就再杀一次
rmdir "${TMPDIR:-/tmp}/oci-a1-retry.lock" 2>/dev/null   # 3. 清理残留的锁
nohup caffeinate -is ./retry.sh < /dev/null >> retry.log 2>&1 &     # 4. 重新启动
sleep 3; tail -3 retry.log                        # 5. 应看到"开始重试"和"尝试创建"
pmset -g assertions | grep -i caffeinate          # 6. 应看到 caffeinate 的断言
```

说明：

- `kill -9` 不会执行 trap，所以**不会清理锁**，第 3 步必须手动做。
- 不带 `-9` 的 `pkill` 最终也会干净退出并自动清理锁，只是可能要等几分钟。
- `pkill -f retry.sh` 会同时匹配到 `caffeinate -is ./retry.sh`，所以停止后务必用 `pgrep -fl retry.sh` 确认没有残留进程。

### 7.4 关掉终端后 `oci` 报 `Bad file descriptor`，连续 5 次后脚本停止

**现象**

脚本一直正常运行，某个时刻起每次尝试都报同样的错，连续 5 次后脚本按设计停止：

```text
[19:29:32] 尝试创建 qdii-arm (1 OCPU / 6GB, 启动卷 50GB)
[19:29:32] 遇到非容量类错误:
Fatal Python error: init_sys_streams: can't initialize sys standard streams
Python runtime state: core initialized
OSError: [Errno 9] Bad file descriptor
[19:29:32] 第 5 次: 未知错误 (连续 1/5)
...
[19:36:27] 第 9 次: 未知错误 (连续 5/5)
[19:36:29] 错误: 连续 5 次非容量类错误，已停止。请检查上面的报错
```

**原因**

- `nohup` 只把标准输出和标准错误重定向到了日志文件，**标准输入仍然连着终端**。
- 关掉终端窗口后，这个终端设备失效。`retry.sh` 本身是 bash，不读标准输入，所以照常运行；但它每次启动 `oci`（一个 Python 程序）时，Python 初始化标准输入输出流就会因为这个失效的描述符直接崩溃。
- 报错发生在 Python 解释器启动阶段，还没有发出任何 API 请求，所以和 OCI 账号、网络、配置都无关。本例中脚本 19:14 启动，19:29 开始报错，时间与关闭终端窗口对得上。

> 这是根据报错内容和时间点做出的判断。如果你遇到同样的报错但从没关过终端，请检查是否有别的东西关闭了终端会话（比如 SSH 断开、终端被重启）。

脚本停止时锁会被自动清理，也没有创建任何实例，重新启动即可。

**修复**

1. `retry.sh` 和 `launch.sh` 开头已加入 `exec < /dev/null`，让所有子进程拿到一个永远有效的标准输入。
2. 启动命令也加上 `< /dev/null`，双保险，即使用的还是旧版脚本也有效。`./ctl.sh` 已默认这样做；手动启动时请使用：

   ```bash
   nohup caffeinate -is ./retry.sh < /dev/null >> retry.log 2>&1 &
   ```

3. 更新脚本需要先停掉旧进程再替换文件：`./ctl.sh down`，替换后 `./ctl.sh up`。

**验证**

启动后关掉终端窗口，再新开一个终端查看日志：

```bash
tail -5 retry.log
```

日志应当继续按 90~120 秒的节奏出现"容量不足"，而不是 `Fatal Python error`。同时用 `./ctl.sh status` 确认进程还在。

### 7.5 修改脚本的注意事项

- **运行中不要替换脚本文件。** bash 是边读边执行的，文件在运行中被替换会导致执行错乱。先停掉脚本，或等它抢到机器自行退出之后再替换。
- **以本地那份为准。** 修改后的文件需要重新下载并覆盖到本地目录，建议把目录放进 git 仓库，改动前先提交一次，出问题可以退回。
- **改完先试跑再上线。** 先执行 `./launch.sh; echo "exit=$?"`，确认仍是 `exit=10`，再启动循环。
- **改 `config.env` 同样要重启。** `retry.sh` 启动时才读取配置，修改后执行 `./ctl.sh restart`。

### 7.6 排查清单

```bash
./ctl.sh status                                     # 两个服务是否在运行
pgrep -fl retry.sh                                  # 脚本是否在运行
tail -20 retry.log                                  # 最近的日志
pmset -g log | grep -E " (Sleep|Wake) +" | tail -20 # 是否休眠过
pmset -g assertions | grep -i caffeinate            # caffeinate 是否生效
ls "${TMPDIR:-/tmp}" | grep oci                     # 锁目录是否存在
```

---

## 8. 常见问题与排错

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
| 提示"已有另一个 retry.sh 在运行" | 旧进程没死或锁残留，按 [7.3](#73-停不掉脚本新启动的脚本立刻退出) 的流程处理。**不要在旧进程还活着时直接删锁** |
| 脚本跑一阵就不动了 | Mac 休眠了，见 [7.1](#71-脚本时断时续mac-休眠)；Cloud Shell 则是空闲断开了 |
| 日志出现 `Fatal Python error ... Bad file descriptor` | 关掉终端后标准输入失效，见 [7.4](#74-关掉终端后-oci-报-bad-file-descriptor连续-5-次后脚本停止) |
| 创建成功但 SSH 连不上 | 见 [5.1](#51-如果连不上检查网络) |
| 想要 2 OCPU / 12GB | 直接抢大规格更难。更稳妥：先抢 1/6，再改名抢第二台 1/6。在已有实例上原地改大也可能遇到容量问题 |
| 想要免费的圣何塞机器 | 不可行。免费资源只能建在 Home Region，Home Region 不能改 |
| 改完 `config.env` 没生效 | `retry.sh` 启动时才读取配置，需要按 [7.3](#73-停不掉脚本新启动的脚本立刻退出) 的流程重启 |

---

## 9. 安全注意事项

- **私钥**（`~/.oci/oci_api_key.pem`、`~/.ssh/oci_qdii`）只留在本机，不要提交到仓库、不要发给任何人。
- **`config.env`** 含 Telegram token 等信息，已被 `.gitignore` 排除，提交前用 `git status` 确认它没有被加入。
- **OCID** 本身不是密钥，别人凭它登不进账号，但也不建议公开。分享日志或截图前可以先打码。
- **一键停止只会终止经过校验的本项目脚本**：进程必须是 bash/caffeinate/python 等预期的程序、最后一个参数是目标脚本，且脚本位于本项目目录，缺一不可，所以不会误杀别处的同名脚本。
- 频率不要调得太激进，脚本强制间隔不低于 60 秒，是为了避免被限流。
