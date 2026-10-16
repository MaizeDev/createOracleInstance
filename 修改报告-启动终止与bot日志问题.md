# 修改报告：修复 `./ctl.sh up` 遥控机器人启动失败 + bot 日志问题

- 诊断报告：[诊断报告-启动终止与bot日志问题.md](诊断报告-启动终止与bot日志问题.md)（已随本次工作保存在项目里）
- 修复前状态存档：commit `007988e`（`chore: 存档修复前状态（含诊断报告、ctl.sh 可执行位）`）
- 修复后改动：**留在工作区，未提交**，方便你先 review（`git diff`）
- 修复时间：2026-10-16 19:55（Asia/Shanghai）

---

## 0. 结论速览

| 问题 | 修复方式 | 验证结果 |
|---|---|---|
| **P0：机器人一启动就误判“已有另一个 tg_control 在运行”并退出** | `procs.primaries()` 把 `caffeinate` 派生的子进程去重，一个服务只算一个主进程；`runner._matched()` 先做这个去重、再排除自己/祖先 | 端到端探针：`BOT.start() → 已启动`，真实 `single_instance()` → “通过，没有误判” |
| **P2：日志里“三条记录”，失败提示混着历史失败** | `start()` 记录启动前日志长度，失败时只贴**本次新增**的输出，并带上退出码 | 探针：`启动后立即退出 (退出码 3)，本次输出: 这是本次启动自己写的输出`（老的 `[老日志]` 行不再被贴出） |
| **P2：`status` 把抢机脚本显示成两个 pid** | 同上（去重） | 真实项目只读 status：`抢机脚本: 运行中 (pid 56143，已运行 0分钟)`（修复前是 `pid 56143,56144`） |
| **P3：长轮询被代理掐断时刷“错误”日志** | `RemoteDisconnected / TimeoutError / ConnectionError / gaierror` 归为“正常现象”，安静重试、恢复时提示一次；连续中断只记第 1 次和每 10 次 | 各类异常分类单测通过；日志文案见 README 排错表 |
| **P3：token 失效（401）静默退避到 300 秒** | 401/403 单独提示“token 可能已失效或被 /revoke”，退避上限从 300s 降到 120s；网络类退避上限 30s | 同上 |
| 长轮询读超时太紧（30s 轮询只留 10s 余量） | `tg_api.py` 读超时改为 `poll_timeout + 20` | — |
| `ps` 读不到进程时是一屏 `PermissionError` 栈 | `procs.snapshot()` 抛出可读错误；`ctl.sh` 顶层转成一句 `出错了: …` | `./ctl.sh status` 在 ps 受限时输出：`出错了: 无法执行 ps 读取进程列表: PermissionError(...)` |
| 残留状态文件 `.tg_control.pid`（旧版遗留，无任何代码引用） | 删除 | 文件已消失，`git status` 无影响（`.gitignore` 里有 `*.pid`） |
| `__pycache__` 未被忽略 | `.gitignore` 增加 `__pycache__/` | — |

**抢机脚本 `retry.sh` 全程未受影响**，修复期间一直在正常重试（只做了只读 status 与探针验证，没有启停你的服务）。

---

## 1. 逐文件改动

### 1.1 `procs.py`（核心修复）

1. 新增 `primaries()`：**丢掉“父进程也在匹配结果里”的进程**。

   ```python
   def primaries(matched):
       ids = {r["pid"] for r in matched}
       return [r for r in matched if r["ppid"] not in ids]
   ```

   原理：`caffeinate -is bash <脚本>` 的真实结构是「主进程 exec 脚本 + fork 一个持有电源断言的子进程」，子进程命令行里仍带脚本名，所以 `find()` 会匹配到两个进程。实测：

   ```
   56143     1  bash .../retry.sh
   56144 56143  caffeinate -is bash .../retry.sh
   pmset: pid 56144(caffeinate) … asserting on behalf of 'bash' (pid 56143)
   ```

2. 新增 `parse_ps()` / `_is_etime()`，把解析逻辑独立出来便于测试；顺带丢弃**命令行含换行的进程被 ps 拆出的续行**（这类伪行进过匹配名单，也会造成误判）。

3. `snapshot()` 把 `ps` 起不来的情况（权限/沙箱）转成清晰报错，不再把“读不到进程”当成“没有进程在跑”（否则单实例检查会静默失效）。

### 1.2 `runner.py`

1. `_matched(rows, primary=True)`：**先去重、再排除自己/祖先**（顺序很重要，见第 3 节）。

   ```python
   found = procs.find(self.patterns, rows)
   if primary:
       found = procs.primaries(found)
   protected = procs.self_and_ancestors(rows)
   return [r for r in found if r["pid"] not in protected]
   ```

2. `stop()` 用 `primary=False`，仍然把 `caffeinate` 子进程一起收掉（实测：只 SIGKILL 主进程时子进程也会自动退出，双保险）。
3. `start()`：启动前记录日志长度，失败时只贴本次新增输出 + 退出码：

   ```
   遥控机器人: 启动后立即退出 (退出码 3)，本次输出:
   这是本次启动自己写的输出
   ```
4. 新增 `_tail_since()`（`_tail()` 仍被 `status()` 使用）。

### 1.3 `tg_control.py`

1. `single_instance()`：判定冲突时改用 `log()`（带时间戳）再 `sys.exit(1)`，不再往 stderr 写无时间戳的裸行。
2. 新增 `is_transient()` / `is_auth_error()`，主循环分类处理：

   - **网络/代理中断** → `长轮询连接被中断 (RemoteDisconnected)，属于代理/网络的正常现象，5 秒后继续（连续第 1 次）`，退避上限 30s，连续中断只记第 1 次和每 10 次，恢复时记一行“长轮询已恢复正常”；
   - **401/403** → 明确提示 token 可能已失效/被 revoke 及处理命令，退避上限 120s；
   - **其它** → 保持原样报错，退避上限由 300s 降到 120s。

### 1.4 `tg_api.py`

`get_updates()` 的读超时由 `poll_timeout + 10` 放宽到 `poll_timeout + 20`（隔代理时 Telegram 可能比 `timeout` 稍晚才回）。

### 1.5 `ctl.py`

顶层加 `KeyboardInterrupt` / 异常处理，出错时打印一句 `出错了: …`（退出码 1），不再甩一屏栈。

### 1.6 其它

- `tg_control.sh`：把顶部过时的 `nohup caffeinate -is ./tg_control.sh …` 手动启动注释改成 `./ctl.sh bot-start/bot-stop`，并注明 caffeinate 会多一个子进程。
- `README.md`：排错表更新“启动后立即退出 (退出码 N)”一行，并新增一行说明“长轮询连接被中断…不用处理”。
- `ctl.py`：帮助文本改成分组版（启停 / 只操作其中一个 / 看日志 / 其它），`coi -h`、`coi --help`、`coi help`、不带参数四种写法都会打印，退出码 0；README 快速开始也加了 `coi -h` 一行。
- 顺带把 README 里的短命令由 `grab` 统一改成 `coi`（项目目录同名），并补上 `coi bot-start`「只启动遥控」说明。
- `.gitignore`：加 `__pycache__/`。
- 删除 `.tg_control.pid`（内容 `55156`，进程早已不存在，全项目无引用）。
- **新增 `test_procs.py`**：12 个离线单测，覆盖去重规则、`ps` 续行过滤、自身/祖先排除、真实机器人进程结构（主进程 + caffeinate 子进程）、启动方仍能看到自己刚启动的服务等。

---

## 2. 验证记录

### 2.1 单测（不需要起进程，随时可跑）

```bash
$ python3 test_procs.py
Ran 12 tests in 0.001s
OK
```

### 2.2 端到端探针（`/tmp/coi-probe`，原样使用修复后的 `procs.py`/`runner.py`/`tg_control.py`，真实进程树 + 真实启动链路）

修复前（诊断阶段复现）：

```
BOT.start() -> 遥控机器人: 启动后立即退出，最近日志:
已有另一个 tg_control 在运行 (pid 57492)
```

修复后：

```
BOT.start() -> 遥控机器人: 已启动
find匹配到: [59040 python3 .../tg_control.py, 59041 caffeinate -is bash .../tg_control.sh]
primaries(去重后): [59040]
BOT.pids() -> []
真实 single_instance() -> 通过，没有误判
BOT.status() -> 遥控机器人: 运行中 (pid 59040，已运行 0分钟)
BOT.stop() -> 遥控机器人: 已强制停止
```

### 2.3 真实项目只读检查

```
$ ./ctl.sh status        # 经 ps 替身执行，只读
抢机脚本: 运行中 (pid 56143，已运行 0分钟)      ← 修复前: pid 56143,56144
最近日志: …
遥控机器人: 未运行
```

修复期间没有对你的抢机脚本/遥控进程做任何启停操作，也没有改动 `config.env`、`retry.log`、`tg_control.log`。

---

## 3. 中途出现并已修掉的一个回归（如实记录）

第一版我给 `_matched()` 加了“连自己的后代一起保护”，结果 **`start()` 的 3 秒自检把“自己刚启动的子进程”也排除了**，启动明明成功却报：

```
遥控机器人: 进程还活着，但在进程列表里找不到它
```

原因是：启动方（`ctl.py`）与服务是父子关系，服务正是它的后代。

修正为：**只做“父子去重”，不加“后代保护”，并且去重必须发生在排除自身之前**（否则自己的 pid 先被剔掉，就认不出“这个 caffeinate 子进程的父进程是我”了）。现在同时满足两件事：

- 服务自检：`find=[自己, caffeinate 子进程] → 去重后[自己] → 排除自己 → “没有别人”`；
- 启动方自检：`find=[服务, caffeinate 子进程] → 去重后[服务] → 服务不是启动方的祖先 → “已在运行”`。

两种情况都有单测锁定（`test_bot_does_not_see_its_own_caffeinate_child`、`test_launcher_still_sees_the_service_it_just_started`）。

---

## 4. 下一步（需要你操作）

1. **启动遥控（我会做的话要发真实 Telegram 消息，所以留给你）**：

   ```bash
   ./ctl.sh bot-start      # 或 ./ctl.sh up（连同抢机脚本）
   ./ctl.sh status         # 应显示“遥控机器人: 运行中 (pid …)”单个 pid
   ```

   手机收到“抢机遥控已上线”即成功；`botlog` 里不应再出现“已有另一个 tg_control 在运行”。

2. **提交修复**（当前改动都在工作区，未提交）：

   ```bash
   git add -A && git commit -m "fix: caffeinate 子进程被误判为另一个实例，机器人无法启动"
   ```

3. **可选清理**：`__pycache__/*.pyc` 在 `80726b5` 里被提交进了版本库，所以每次运行都会在 `git status` 里显示改动。`.gitignore` 已加 `__pycache__/`，但要彻底不再跟踪需要：

   ```bash
   git rm -r --cached __pycache__
   ```

   我没有动 git 索引，避免影响你自己的提交流程。

4. **回滚**：本次修复不涉及数据/配置，`git checkout -- procs.py runner.py tg_control.py tg_api.py ctl.py tg_control.sh README.md .gitignore && rm test_procs.py` 即可回到 `007988e` 的状态（`.tg_control.pid` 是旧残留，建议不要恢复）。

---

## 5. 本次未动的项（诊断报告里列出，但风险/收益不划算，保留给你决定）

| 项 | 说明 |
|---|---|
| `procs.py` 的 `cwd_of()` 每个候选进程起一次 `lsof` | 只在“相对路径启动的脚本”上触发；当前 `ctl.sh` 都用绝对路径，实测不影响。想优化可批量查 |
| `mentions()` 不校验目录 | 别的目录有同名 `retry.sh` 时会拒绝启动，属保守设计，README 已有说明 |
| `wait_gone()` 每 0.2s 全量 `ps` | 最多 10 秒，实测无感 |
| `MAX_AGE_SEC=120` 静默丢弃过期命令 | 安全设计，只在 README 里说明 |
| 直接删掉 `single_instance()` | 它与 `BOT.start()` 的检查重复，但作为“手动启动第二个实例”的拦截仍有价值，已修好而不是删掉 |
