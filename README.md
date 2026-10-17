# oci-a1-retry

自动重试创建 Oracle Cloud **Always Free Arm** 实例（`VM.Standard.A1.Flex`），抢到后 Telegram 通知；不在电脑边也能用 Telegram 遥控启停。

只依赖官方 OCI CLI + bash + curl + Python 标准库，不碰你的账号密码，不需要任何第三方服务。

- 容量不足 / 被限流自动放慢，连续异常自动停止并通知
- 一条命令启停（`coi up / status / log / down`），macOS 自动防休眠，关终端不中断
- 可选 Telegram：抢到通知 + 3 条固定命令的遥控（只认你本人）

---

## 1. 需要什么

- **macOS**（防休眠用了 `caffeinate`）。Linux 也能运行：没有 `caffeinate` 时自动跳过防休眠，其余功能不受影响
- **OCI CLI 已配置**：`oci iam region list` 能返回列表
- **python3**（只用标准库，不用装包）
- Always Free 额度：A1 合计 **2 OCPU / 12GB**、存储 **200GB**；免费资源只能建在 **Home Region**，注册后不可改

额度要点：A1 和 AMD 是两份独立额度；**存储容易超**（2 台 ARM 各 50GB + 2 台 AMD 各 47GB 就快到 200GB 上限）；超出免费额度的 A1 在试用期结束后会被停用、随后删除，长期几乎无负载的实例也可能被回收。

> 抢不到的预期：热门区域（如新加坡只有 AD-1）可能要等几小时到几天。想提高优先级可以升级 Pay As You Go——只要用量不超过免费额度就不收费。

---

## 2. 快速开始（5 步）

**① 安装并配置 OCI CLI，上传 API 公钥**

```bash
brew install oci-cli
oci setup config
cat ~/.oci/oci_api_key_public.pem   # 复制全部内容
```

控制台右上角头像 → **My profile** → **API keys** → **Add API key** → **Paste a public key**，粘贴后指纹应与终端显示的一致。

- 私钥密码提示时输入 **`N/A`**（不设密码）——直接回车会反复提示。脚本要无人值守运行，私钥不能加密。
- 区域提示选 `ap-singapore-1`（**不是** `ap-singapore-2`，那是另一个区域），其余提示回车用默认值即可。
- 验证：`oci iam region list` 能返回区域列表；报文件权限过宽用 `oci setup repair-file-permissions --file ~/.oci/config`（私钥同理）。

**② 生成登录用的 SSH 密钥**（和上面的 API 密钥是两回事）

```bash
ssh-keygen -t ed25519 -f ~/.ssh/oci_qdii
```

**③ 填配置**

```bash
cp config.env.example config.env
./lookup.sh     # 输出 COMPARTMENT_ID / AVAILABILITY_DOMAIN / SUBNET_ID / IMAGE_ID
```

把 4 个 OCID 填进 `config.env`，必填项与默认值见 [配置](#4-配置)。输出里夹着的 `SyntaxWarning` 和 `supports pagination` 是 OCI CLI 自己的提示，可以忽略。

**④ 单次试跑**

```bash
./launch.sh; echo "exit=$?"
```

| 退出码 | 含义 |
| --- | --- |
| `0` | 创建成功，或同名实例已存在（看输出里的 IP） |
| `10` | 容量不足 → **参数都对，可以开始循环了** |
| `11` | 被限流 → 隔几分钟再试 |
| `1` | 其他错误 → 看打印出来的报错原文 |

**⑤ 开始循环**

```bash
./ctl.sh up
```

抢到后脚本自动退出、打印公网 IP 和登录命令（配了 Telegram 会推送）。

---

## 3. 命令

建议设置短命令（之后任意目录可用；项目目录本身就叫 `coi`，正好同名）：

```bash
echo "coi() { '$PWD/ctl.sh' \"\$@\"; }" >> ~/.zshrc && source ~/.zshrc
```

| 命令 | 作用 |
| --- | --- |
| `coi up` / `down` | 一键启动 / 停止：抢机脚本 + Telegram 遥控 |
| `coi restart` | 两个服务都重启（改了抢机脚本 / 配置后用） |
| `coi status` | 查看两者状态，抢机附带最近几行日志 |
| `coi start` / `stop` | 只启动 / 停止抢机脚本 |
| `coi bot-start` / `bot-stop` | 只启动 / 停止遥控 |
| `coi bot-restart` | 只重启遥控（**改完机器人代码/配置后用这条载入新代码**，不打断抢机） |
| `coi log` / `botlog` | 实时看日志（`Ctrl+C` 只是退出查看） |
| `coi -h` | 全部命令和说明 |

`status` 长这样（终端自动上色，管道/重定向里自动关闭；`--no-color` 或 `NO_COLOR=1` 可关）：

```text
  ── 抢机脚本 ──────────────────────────────
    运行中 · pid 60042 · 已运行 12分钟
    最近日志:
      [2026-10-16 20:19:11] 尝试创建 qdii-arm (1 OCPU / 6GB, 启动卷 50GB)

  ── 遥控机器人 ────────────────────────────
    运行中 · pid 59441 · 已运行 13分钟
```

`up` / `down` / `restart` 是每服务一行；失败时把**本次**启动新写入的日志缩进跟在后面（不会混进历史失败）。没配 Telegram 时 `up` 会提示遥控启动失败，但抢机脚本照常启动（也可只用 `coi start`）。

`ctl.sh` 替你做的事：自动 `caffeinate` 防休眠、独立会话（关终端不中断）、防重复启动、自动清理残留锁、强制停止（连 `oci` 子进程一起收掉）、只认本项目目录的脚本（不会误杀别处同名脚本）。手动启动的 `nohup ./retry.sh ...` 也能被它识别和停掉。

---

## 4. 配置

**必填**（`./lookup.sh` 会给你）：

| 配置项 | 说明 |
| --- | --- |
| `COMPARTMENT_ID` | 根区间 = 租户 OCID |
| `AVAILABILITY_DOMAIN` | 新加坡是 `uMAA:AP-SINGAPORE-1-AD-1`（前缀以输出为准） |
| `SUBNET_ID` | 子网 OCID |
| `IMAGE_ID` | Ubuntu aarch64 镜像 OCID（`OS_VERSION=22.04 ./lookup.sh` 可换版本） |

`SSH_PUBLIC_KEY_FILE` 必填，写 `$HOME/.ssh/oci_qdii.pub`（**不要用 `~`**，引号里不会展开）。

**可选**（括号内为默认值）：`INSTANCE_NAME`（`qdii-arm`）、`OCPUS`（`1`）、`MEMORY_GB`（`6`）、`BOOT_VOLUME_GB`（`50`）、`INTERVAL_SEC`（`90`）、`JITTER_SEC`（`30`）、`MAX_ATTEMPTS`（`0`=不限）、`MAX_UNKNOWN_ERRORS`（`5`）、`TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID`、`TG_CMD_MAX_AGE_SEC`（`120`）、`TG_LOG_WIDTH`（`40`）。

- 默认抢 **1 OCPU / 6GB**：比一上来就要 2/12 容易成功得多。
- 有**额度闸**：`OCPUS > 2`、`MEMORY_GB > 12`、启动卷不在 47~200、间隔低于 60s 会直接拒绝运行（但不会统计账号里已有的实例，要自己算）。
- **改完 `config.env` 要重启**：`coi restart`。改了 `TELEGRAM_*` 也要用 `coi restart`：`retry.sh` 启动时只读一次配置，它发出的“连续异常停止 / 达到最大次数”通知用的是启动时的值，只重启遥控不会更新。只有遥控专用的项（`TG_CMD_MAX_AGE_SEC`、`TG_LOG_WIDTH`）才可以只用 `coi bot-restart`。

---

## 5. Telegram（可选）

### 5.1 通知

1. 找 @BotFather 发 `/newbot` 拿 token；**先给机器人发一条消息**（否则它不能主动发给你）。
2. `TELEGRAM_CHAT_ID` 是你的**数字用户 ID**（不是 @用户名）。看不到就打开 `https://api.telegram.org/bot<token>/getUpdates`，找 `"chat":{"id":...}`。
3. 填进 `config.env` 后手动测一遍（`notify.sh` 自己不读配置，只看环境变量）：

```bash
set -a; . ./config.env; set +a
./notify.sh "测试通知"
```

会推送：抢到机器、连续异常导致停止、达到 `MAX_ATTEMPTS`。普通“容量不足”不推送；发送失败不影响重试。

### 5.2 遥控（只支持 3 条命令）

| 命令 | 作用 |
| --- | --- |
| `/grab_start` | 启动抢机（已在跑则提示，不会起第二份） |
| `/grab_stop` | 强制停止并清理锁 |
| `/grab_status` | 查看状态 + 最近几行日志（只读） |

- 启动：`coi bot-start`（`coi up` 会一起启动）。菜单按钮自动注册，**只对你本人可见**。
- 手机回复长这样（日志行会压短到手机上不折行；`TG_LOG_WIDTH` 可调）：

```text
🟢 抢机脚本 运行中
pid 60042 · 已运行 12分钟

📄 最近日志
[20:31:09] 尝试创建 qdii-arm（1C/6GB）
```

- **安全设计**：只认私聊里 `TELEGRAM_CHAT_ID` 那个数字 ID；消息必须**恰好**是一条命令（多余内容一律忽略，文本不会拼进命令行）；启动时丢弃积压旧消息、超过 120 秒的忽略；token 不进日志、不进 `ps` 命令行；重复启动会被拒绝。
- **加固**：@BotFather 里 `/setjoingroups` → Disable；token 泄露就 `/revoke`，更新 `config.env` 后 `coi restart`（抢机脚本里的通知也要换成新 token）。**不要把 token 贴到聊天或截图里。**
- 遥控是**主动向外长轮询**，不需要公网 IP、不用开端口；但电脑要醒着、遥控进程要在跑，否则发消息没反应（需要回电脑 `coi bot-start`）。

---

## 6. 抢到之后

```bash
ssh -i ~/.ssh/oci_qdii ubuntu@<公网IP>
```

连不上先查网络（三处都要通）：VCN 里有 **Internet Gateway**；子网**路由表**有 `0.0.0.0/0` 指向它；**Security List** 入站放行 TCP 22。

<details>
<summary>用 CLI 一次查完（点开）</summary>

```bash
T=<租户OCID>; V=<VCN的OCID>; S=<子网OCID>
oci network internet-gateway list -c $T --vcn-id $V \
  --query 'data[].{name:"display-name",state:"lifecycle-state",enabled:"is-enabled"}' --output table
oci network subnet get --subnet-id $S --query 'data."route-table-id"' --raw-output
oci network route-table list -c $T --vcn-id $V \
  --query 'data[].{name:"display-name",rules:"route-rules"}' --output json
oci network security-list list -c $T --vcn-id $V \
  --query 'data[].{name:"display-name",ingress:"ingress-security-rules"}' --output json
```
</details>

- 开 80/443：**Security List 和 Ubuntu 自带的 iptables 两处都要放行**，只改一处不生效。
- 抢第二台：改 `INSTANCE_NAME` 再跑。两台 ARM 合计不能超 2 OCPU / 12GB；同一时间只能跑一份 `retry.sh`。

---

## 7. 它是怎么工作的

| 情况 | 行为 |
| --- | --- |
| 容量不足（`Out of host capacity`） | 等 `INTERVAL_SEC` + 随机 1~`JITTER_SEC` 秒后重试 |
| 被限流（429 / `TooManyRequests`） | 等待时间 ×4 |
| 其他错误 | 计数，连续 `MAX_UNKNOWN_ERRORS` 次停止并通知；中间遇到容量不足 / 限流会清零 |
| 成功 | 等实例进入 RUNNING → 打印公网 IP → 退出 |

防重复：每次尝试前按 `INSTANCE_NAME` 查同名未销毁实例，有就不再创建；锁目录防并发。日志里每 90~120 秒一行“容量不足”是正常的。

---

## 8. 排错速查

| 现象 | 处理 |
| --- | --- |
| `exit=10` / 每 90~120 秒“容量不足” | 正常，继续等 |
| “被限流，延长等待”（约 6 分钟） | 正常，**别把间隔调小** |
| 偶尔 `connection timed out` | 网络波动 / 刚唤醒，通常自愈 |
| 日志间隔变成几小时、“单次尝试耗时”几十分钟 | Mac 休眠过：接电源 + 不合盖（见下） |
| 提示“已有另一个 retry.sh 在运行” | 旧进程没死 / 锁残留：`coi restart`（**别在旧进程活着时手删锁**） |
| `Fatal Python error ... Bad file descriptor` | 关终端导致 stdin 失效：用 `coi up` 启动；手动要加 `< /dev/null` |
| `NotAuthenticated` / 401 | 公钥没上传或 fingerprint 不一致；检查 `~/.oci/config` |
| `LimitExceeded` | 免费额度用完：已有 A1 实例，或存储超 200GB |
| `409 Conflict`（botlog） | 别处也在拉同一个 bot（webhook / 另一台机器） |
| botlog 里“长轮询连接被中断 (RemoteDisconnected)” | 代理 / 网络正常现象，几秒自动重连，不用管 |
| 手机发命令没回复 | `coi status` 看遥控是否在跑；`coi botlog` 看日志 |
| 改完 `config.env` 没生效 | 要重启：`coi restart` / `coi bot-restart` |
| 改脚本后行为怪 | 运行中不要替换脚本文件；先停再改 |

<details>
<summary>三个常见坑：原因与确认方法（点开）</summary>

**① Mac 休眠 → 脚本“时断时续”**

- 原因：休眠期间进程被冻结、墙上时钟照走，于是日志里出现“耗时 22 分钟”“间隔 1.5 小时”。锁屏不是原因（锁屏是休眠的结果）。
- 确认：`pmset -g log | grep -E " (Sleep|Wake) +" | tail -20` 与日志对照；`DarkWake` 不代表脚本在正常运行。
- 处理：`coi up` 已自动带 `caffeinate -is`；必须**接电源 + 不合盖**（合盖会强制睡眠，拦不住）。验证：`pmset -g assertions | grep -i caffeinate` 应看到 `caffeinate asserting on behalf of 'bash' (pid N)`；每个服务对应 **2 条**（`PreventUserIdleSystemSleep` + `PreventSystemSleep`，即 `-i` 和 `-s`），`pid N` 是 `retry.sh` 的进程号（可和 `coi status` 里的 pid 对照）。

**② 停不掉 / 新进程立刻退出**

- 原因：`retry.sh` 的 trap 在等 `sleep`/`oci` 期间会推迟处理 SIGTERM（限流时一次等待约 6 分钟），所以 `pkill` 看起来“没反应”；旧进程还活着时新进程必然撞锁。
- 处理：`coi restart`（强制终止 + 清锁 + 重启）。手动兜底顺序不能颠倒：`pkill -9 -f retry.sh` → `pgrep -fl retry.sh` 确认没了 → `rmdir "${TMPDIR:-/tmp}/oci-a1-retry.lock"` → 重新启动（`kill -9` 不执行 trap，必须手动清锁）。

**③ 关终端后 `oci` 报 Bad file descriptor**

- 原因：`nohup` 只重定向了 stdout/stderr，stdin 还连着终端；终端关掉后 Python 初始化标准流就崩（还没发出任何 API 请求，和账号 / 网络无关）。
- 处理：`retry.sh` / `launch.sh` 开头已 `exec < /dev/null`；手动启动也加 `< /dev/null`。
</details>

<details>
<summary>排查清单（点开）</summary>

```bash
coi status                                          # 两个服务是否在运行
pgrep -fl retry.sh                                  # 抢机脚本进程
tail -20 retry.log                                  # 最近的抢机日志
pmset -g assertions | grep -i caffeinate            # caffeinate 是否生效
pmset -g log | grep -E " (Sleep|Wake) +" | tail -20 # 是否休眠过
ls "${TMPDIR:-/tmp}" | grep oci                     # 锁目录是否存在
```
</details>

---

## 9. 安全

- 私钥（`~/.oci/oci_api_key.pem`、`~/.ssh/oci_qdii`）只留本机，不要提交、不要外发。
- `config.env` 含 token 等信息，已被 `.gitignore` 排除；提交前用 `git status` 确认。
- OCID 本身不是密钥（别人凭它登不进账号），但也不建议公开。
- 一键停止只终止**经过校验、位于本项目目录**的脚本，不会误杀别处同名脚本。
- 间隔不要低于 60 秒，脚本会强制拒绝。

---

## 10. 文件与测试

日常只用 `coi`；其余：

| 文件 | 作用 |
| --- | --- |
| `ctl.sh` / `ctl.py` | 一键启停入口（`ctl.py` 是实际实现） |
| `retry.sh` / `launch.sh` | 循环重试 / 单次创建尝试 |
| `common.sh` | 公共函数：读配置（`load_env` / `load_config`）、查实例、成功后汇报 |
| `lookup.sh` | 只读查询，找出要填的 OCID |
| `notify.sh` | Telegram 通知（token 走 curl stdin，不进 `ps` 命令行） |
| `tg_control.sh` / `tg_control.py` | 遥控入口 / 主程序 |
| `runner.py` / `procs.py` | 启停逻辑与进程识别（`ctl.sh` 和机器人共用） |
| `tg_api.py` | Telegram Bot API 的最小封装 |
| `config.env.example` | 配置模板，复制为 `config.env` |

回归测试（不联网、不启动任何服务）：

```bash
python3 test_procs.py      # 进程识别 / 去重 / 终端与聊天渲染
python3 test_hardening.py  # 加固项：token 不进 argv、ps 读空报错、路径含空格、bash 3.2 花括号
python3 test_launch.py     # launch.sh 退出码约定 + 成功路径通知
```
