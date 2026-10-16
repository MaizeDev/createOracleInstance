# 诊断报告：`./ctl.sh up` 遥控机器人启动失败 + 状态查询后日志报错

- 审查对象：`ctl.sh` / `ctl.py` / `runner.py` / `procs.py` / `tg_control.py` / `tg_control.sh` / `tg_api.py`
- 对应改动：commit `80726b5`（“优化启动/终止命令和bot”，2026-10-16 18:49）
- 审查方式：静态审查 + 在 `/tmp` 用探针原样复现（**未修改项目内任何代码**）+ 读取当前真实进程树/日志
- 时间基准：2026-10-16 19:20 前后（Asia/Shanghai）

---

## 0. 结论速览

| # | 现象 | 严重度 | 根因（一句话） | 是否本次改动引入 |
|---|---|---|---|---|
| 1 | `./ctl.sh up` 后“遥控机器人: 启动后立即退出”，日志刷 `已有另一个 tg_control 在运行 (pid …)` | **P0 阻断** | `caffeinate` 会 fork 一个**子进程**继续持有电源断言，它的命令行里仍带 `tg_control.sh`；`procs.find()` 把它当成“另一个 tg_control”，而 `runner.Service._matched()` 只排除“自己+祖先”，**不排除自己的后代** → `tg_control.single_instance()` 必然误判自杀 | **是**（新引入） |
| 2 | 日志里一次“三条记录” | P2 误导 | 不是报三次，而是 `runner._tail(log_file, 3)` 把**同一个追加日志文件的最后 3 行**贴出来；每次失败的 `up` 都会往这个文件追加 1 行（无时间戳，见下） | 是（放大） |
| 3 | `/grab_status` 约 1 分钟后日志出现 `RemoteDisconnected` / `TimeoutError` | P3 噪音 | bot 的 HTTPS 请求全部走本机系统代理 **Surge 127.0.0.1:6152**（urllib 在 macOS 上会自动读取系统代理），长轮询连接被代理/网络断开；代码已自动重试并自愈，只是**把正常事件当错误打印** | **否**（旧版本就有） |
| 4 | `./ctl.sh status` 里抢机脚本显示成两个 pid（`pid 56143,56144`） | P2 | 与 #1 同一个根因（caffeinate 子进程被重复计数） | 是 |

**一句话总结：#1 是唯一真正坏掉的地方——只要经 `./ctl.sh up|bot-start` 启动（会套一层 `caffeinate`），遥控机器人**永远**起不来；抢机脚本本身没坏，还在正常跑。**

---

## 1. 证据链

### 1.1 真实进程树（就在你这台机器上实测）

```
$ ps -axww -o pid=,ppid=,command= | grep -E 'retry\.sh|caffeinate -is'
56143     1  bash /Users/wheat/Downloads/coi/retry.sh
56144 56143  caffeinate -is bash /Users/wheat/Downloads/coi/retry.sh     ← ppid = 56143，是子进程
```

`pmset` 也印证了这两个进程的分工：

```
pid 56144(caffeinate): PreventUserIdleSystemSleep named: "caffeinate command-line tool"
    Details: caffeinate asserting on behalf of 'bash' (pid 56143)
pid 56144(caffeinate): PreventSystemSleep named: "caffeinate command-line tool"
```

即：`caffeinate` 把**真正的工具进程** exec 成主进程（pid 56143，runner 跟踪到的就是它），再 fork 一个**子进程**（pid 56144）持有电源断言。这个子进程的 argv 一直保持为 `caffeinate -is bash /path/xxx.sh`。

### 1.2 用探针复现（`/tmp/coi-probe`，原样复制 `procs.py`/`runner.py` + 同名 `tg_control.sh`/`tg_control.py`）

```
$ PATH=/tmp/coi-probe/bin:$PATH python3 /tmp/coi-probe/driver.py
BOT.start() -> 遥控机器人: 启动后立即退出，最近日志:
已有另一个 tg_control 在运行 (pid 57492)
--- 探针内部诊断 ---
{
 "my_pid": 57491,
 "matched_by_find": [
  {"pid": 57491, "ppid": 57487, "cmd": ".../Python .../tg_control.py"},
  {"pid": 57492, "ppid": 57491, "cmd": "caffeinate -is bash .../tg_control.sh"}   ← 自己的子进程
 ],
 "protected_self_and_ancestors": [1, 56894, 56922, 57481, 57487, 57491],
 "BOT.pids()": [57492]
}
```

和你截图里的输出**一字不差**。

### 1.3 同一个 bug 的另一处表现（真实项目，只读 status）

```
抢机脚本: 运行中 (pid 56143,56144，已运行 0分钟)
```

抢机脚本只有一个进程，却被算成两个 —— 多出来的 `56144` 就是 caffeinate 子进程。

---

## 2. 根因分析（带代码位置）

### 2.1 成因链

1. `runner.py:74-83` 启动服务时，如果系统里有 `caffeinate`，命令是
   `caffeinate -is bash <脚本>`；`caffeinate` exec 主进程 + fork 一个持有断言的子进程。
2. `procs.py:16`：`SHELL_EXES = {"bash", "sh", "zsh", "caffeinate", "nohup"}`
   → 这个 caffeinate 子进程**算合法匹配**。
3. `procs.py:69-90` `find()`：只看 **命令行最后一个参数**是不是目标脚本名
   → caffeinate 子进程最后一段正是 `/Users/wheat/Downloads/coi/tg_control.sh`，并且目录校验通过（`_in_project`），所以被匹配。
4. `runner.py:44-46` `_matched()`：

   ```python
   def _matched(self, rows):
       protected = procs.self_and_ancestors(rows)     # 只往上找 自己 + 祖先
       return [r for r in procs.find(self.patterns, rows) if r["pid"] not in protected]
   ```

5. `procs.py:106-113` `self_and_ancestors()` 沿 **ppid 往上**走，**不会**包含自己的后代。
   → caffeinate 子进程是「自己的儿子」，不在保护集合里。
6. `tg_control.py:51-55`：

   ```python
   def single_instance():
       others = runner.BOT.pids()          # → [caffeinate 子进程]
       if others:
           sys.exit("已有另一个 tg_control 在运行 (pid %s)" % ...)
   ```

   → 每次启动都在“发现自己”之后退出。
7. `runner.py:86-89`：`start()` 等 3 秒后看到进程已退出，于是打印
   `启动后立即退出，最近日志:` + `_tail(log_file, 3)`。

因为第 6 步是在**任何正常启动逻辑之前**执行的，所以只要带 caffeinate，就是 100% 必然失败（不是偶发）。

### 2.2 为什么日志里是“三条记录”

- `runner.py:16-25` `_tail(path, n=3)` 取的是**整个日志文件的最后 3 行**；`tg_control.log` 是所有历史启动共享的追加文件。
- 每次失败退出走的是 `sys.exit("…")`，字符串写到 **stderr**（被 `runner.py:80-82` 的 `stderr=STDOUT` 追加进同一个日志），**不经过 `tg_control.py:34-38` 的 `log()`**，所以那三行没有 `[时间]` 前缀。
- 实测日志尾部正是三次失败（三个不同 pid），不是一次报三次：

  ```
  30  已有另一个 tg_control 在运行 (pid 55971)
  31  已有另一个 tg_control 在运行 (pid 56061)
  32  已有另一个 tg_control 在运行 (pid 56169)
  ```

  每多按一次 `up` 就多一行；`_tail(…,3)` 每次都把最近三行端出来，所以看起来像“一堆重复错误”。

补充：日志第 19、21 行的 `(pid 54761)`（18:14、18:19）是**真的冲突**——当时旧的遥控进程还活着（正卡在 401 退避里，见 18:09–18:19 那串日志），和 18:5x 之后的三条误报性质不同。

### 2.3 影响面

- ✅ 抢机脚本 `retry.sh` 正常（当前 pid 56143，日志持续在重试）。
- ❌ 遥控机器人**无法通过 `ctl.sh` 启动**（`up` / `bot-start` / `restart` 全部受影响）→ 手机上 `/grab_*` 无人应答。
- ⚠️ `status` 把抢机脚本显示成两个 pid（同一根因，观感问题）。
- ✅ `down` / `stop` 目前是**对的**：两个 pid 都被杀掉，不会残留（实测 SIGKILL 主进程后 caffeinate 子进程会自动退出）。

---

## 3. 第二个问题：`RemoteDisconnected` / `TimeoutError`

### 3.1 事实

- bot 的 HTTP 走标准库 `urllib`（`tg_api.py:15-32`），长轮询参数 `timeout=30`、socket 读超时 `30+10=40s`。
- **Python 在 macOS 上会自动读取系统代理**（`urllib.request.getproxies()` → `_scproxy`）。实测你机器上：

  ```
  urllib 探测到: {'http': 'http://127.0.0.1:6152', 'https': 'http://127.0.0.1:6152', 'socks': 'http://127.0.0.1:6153'}
  lsof: Surge 66666 wheat TCP 127.0.0.1:6152 (LISTEN)
  ```

  也就是说：**每一次 `getUpdates` 长轮询都要先经过 Surge**。长轮询连接被 Surge（或上游/NAT）掐断时，客户端看到的就是 `RemoteDisconnected('Remote end closed connection without response')`；超过 40s 没读到响应就是 `TimeoutError`。
- 代码对此是**容错的**：`tg_control.py:136-139` 捕获后按 5s→10s→…→300s 退避重试，`offset` 不丢，消息不会重复/丢失。日志可直接证明自愈：18:25:40 报错后，18:34:49、18:36:24 仍然正常执行了命令。
- “执行命令后过 1 分钟才出现”属于**巧合**：一次长轮询约 30s，命令处理完立刻进入下一轮，所以任何连接层故障通常会在 1~2 个轮询周期后浮现。18:51:09 那次报错之前 15 分钟没有任何命令，也照样发生了。

### 3.2 需要改的不是“修网络”，而是“别把它当错误”

- 这两种异常是**长轮询的正常代价**（尤其还隔着代理），当前按错误级别打印，容易让人误以为程序出故障。
- 另有两点值得改：
  - `tg_api.py:32`：读超时只比轮询多 10s（40s），Telegram 在负载高时可能 >30s 才回，导致 `TimeoutError`；建议放宽到 `poll_timeout + 20~30`。
  - `HTTPError 401`（18:09–18:19）退避到 **300 秒**且静默重试：token 错了会“安静地装死 5 分钟”。建议 401/403 不走长退避，而是明确告警或直接退出。

---

## 4. 其他审查发现（建议一并处理）

| 位置 | 问题 | 建议 |
|---|---|---|
| `_tail()` 取整文件尾 3 行 | 失败提示会把**历史失败**当成本次失败展示，误导排查（就是“三条记录”的观感来源） | 启动前先记录日志文件大小/偏移，失败时只展示**本次新增**的输出，并附进程退出码 |
| `tg_control.py:55` `sys.exit(str)` | 走 stderr，绕过 `log()`，日志里出现无时间戳的裸行 | 改成 `log(...)` 后 `sys.exit(1)`，或在 `tg_control.sh` 里统一加时间戳 |
| `runner.py:135-138` `BOT` 无 `lock_dir` | `stop()` 无法清理残留状态文件 | 见下条 |
| 项目根目录 `.tg_control.pid`（内容 `55156`） | 新代码**既不读也不写**它（全项目 grep 无引用），是旧版残留；`:55156` 进程早已不存在 | 直接删除；`.gitignore` 里已有 `*.pid`，但旧文件不会被自动清理 |
| `procs.py:19-36` `snapshot()` 用 `line.split(None, 4)` | 若某进程 argv 里含**换行**（如 `bash -c $'a\nb'`），ps 输出会多出几行，被解析成“伪进程行”，可能污染 `mentions()` 的判断 | 解析时校验 `etime` 形如 `[[dd-]hh:]mm:ss`，不合法则视为上一行的延续 |
| `procs.py:39-55` `cwd_of()` | 相对路径匹配时每个候选进程都要起一次 `lsof`（5s 超时），进程多时慢 | 只在需要时查，或批量查询 |
| `procs.py:93-103` `mentions()` | 不校验目录，别的目录里有同名脚本在跑就会拒绝启动 | 文档里说明，或加白名单/提示如何确认 |
| `procs.py:138-146` `wait_gone()` | 每 0.2s 全量 `ps`，最多 10s（约 50 次） | 可接受，知悉即可 |
| `tg_control.sh:3` 顶部注释 | 还写着旧的 `nohup caffeinate -is ./tg_control.sh < /dev/null >> tg_control.log 2>&1 &` 手动启动方式，与现在的 `ctl.sh` 设计不一致 | 更新注释，避免手动启动时踩同一个 caffeinate 坑 |
| `tg_control.py:18` `MAX_AGE_SEC=120` | 电脑休眠/离线期间积压的命令超过 2 分钟会被静默丢弃（只记一行“忽略过期消息”） | 安全上合理，但建议在 README 里写清 |
| `__pycache__/*.pyc` 被提交进 git（`80726b5` 新增），`.gitignore` 未忽略 `__pycache__/` | 每次运行都会改动被跟踪的二进制文件，`git status` 一直脏 | `.gitignore` 加 `__pycache__/`，并从版本库移除 |
| `single_instance()` 依赖 `ps` 扫描 | 若 `ps`/`lsof` 执行失败（沙箱、权限受限），`snapshot()` 返回空 → 守卫**静默失效**，可能真的同时跑起两个 bot | 扫描失败要显式报错/退出，而不是当成“没有其它实例” |

---

## 5. 修复建议（**尚未改代码**，等你确认）

### 方案 A（最小改动，直击 P0）

`runner.py:44-46`，把“自己的后代”也加入保护集合：

```python
def _matched(self, rows):
    protected = procs.self_and_ancestors(rows)
    protected |= set(procs.with_descendants([os.getpid()], rows))   # 新增：自己的后代也不算“别人”
    return [r for r in procs.find(self.patterns, rows) if r["pid"] not in protected]
```

实测效果：`BOT.pids()` 由 `[57492]` 变成 `[]`，机器人正常启动。

### 方案 B（推荐，根治“一个服务算成两个进程”）

在 `procs.find()` 的结果上做**父子去重**：如果一个匹配到的进程，它的父进程也在匹配结果里，就丢掉它（只保留每个服务树的“主进程”）。

```python
def primaries(matched):
    ids = {r["pid"] for r in matched}
    return [r for r in matched if r["ppid"] not in ids]
```

实测效果：

- 机器人自检：`find=[主进程, caffeinate 子进程]` → 主进程（=自己）→ 不再误判；
- 抢机脚本 `status`：`find=[56143,56144]` → `[56143]`，状态显示恢复正常；
- 只 `SIGKILL` 主进程后 caffeinate 子进程会**自动退出**，不会残留（已实测），所以去重不影响 `stop`。

建议 A+B 一起用：A 保证守卫永不自伤，B 让状态显示/统计正确。同时 `stop()` 内部仍可用完整匹配集合，保证把 caffeinate 子进程也显式收掉。

### 其他（见第 4 节表格）

优先做三件：① 失败提示只输出本次新增日志 + 退出码；② `sys.exit` 的消息带时间戳；③ `RemoteDisconnected`/`TimeoutError` 降级为正常事件、401 不再退避 5 分钟。

---

## 6. 你可以自己验证的命令

```bash
# 1) 看进程结构：注意第二行的 ppid 就是第一行
ps -axww -o pid=,ppid=,command= | grep -E 'retry\.sh|caffeinate -is'

# 2) 看断言是哪个 pid 持有的（会显示 "asserting on behalf of 'bash' (pid …)"）
pmset -g assertions | grep -A2 -i caffeinate

# 3) 复现误报：立刻就会打印“已有另一个 tg_control 在运行 (pid …)”
./ctl.sh bot-start

# 4) 看抢机脚本被算成几个 pid（当前会出现 "pid A,B" 两个）
./ctl.sh status
```

复现环境（我用的探针，在 /tmp，不涉及你的项目文件）：`/tmp/coi-probe/`，其中 `bin/ps` 用 `sysctl(KERN_PROCARGS2)` + `lsof` 复刻了被沙箱拦截的 `/bin/ps`，`probe/` 是 `procs.py`/`runner.py` 的副本加同名探针脚本。

---

## 7. 附：本次未做的事

- 未修改 `coi/` 下任何代码或配置（`git status` 与审查前一致）。
- 未启停你的抢机脚本/遥控服务；只做了只读的 `status`、进程/日志/代理检查。
- `/tmp/coi-probe` 是临时目录，可随时 `rm -rf /tmp/coi-probe` 删除。
