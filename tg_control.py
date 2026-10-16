#!/usr/bin/env python3
"""Telegram 遥控入口。

安全模型，任何一条不满足就直接忽略（不回复，也不暴露任何信息）:
  1. 必须是私聊，且发送者的数字 ID 等于 TELEGRAM_CHAT_ID。
     只看数字 ID，不看用户名，因为用户名可以随时修改。
  2. 消息不能太旧，防止积压的旧命令被重放。
  3. 消息必须恰好是一条白名单命令，多余内容一律无效；文本不会被拼进任何命令行。
"""
import http.client
import os
import socket
import sys
import time
import urllib.error

import runner
from tg_api import TelegramApi

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
MAX_AGE_SEC = int(os.environ.get("TG_CMD_MAX_AGE_SEC", "120"))

# 长轮询隔着代理/网络跑，连接被掐断是常态: 这几个异常不算故障，安静重试即可
TRANSIENT_ERRORS = (
    http.client.RemoteDisconnected,  # 代理/服务器把长轮询连接关了
    TimeoutError,                    # 读超时
    ConnectionError,                 # 连接被重置等
    socket.gaierror,                 # DNS 抖动
)

COMMANDS = {
    "/grab_start": runner.start,
    "/grab_stop": runner.stop,
    "/grab_status": runner.status,  # 只读
}
# 菜单按钮里显示的命令（去掉斜杠，写法要求: 小写字母/数字/下划线）
MENU = [
    ("grab_start", "🚀 开始抢机"),
    ("grab_stop", "🛑 停止抢机"),
    ("grab_status", "📊 查看状态"),
]
HELP = ("📖 可用命令\n"
        "/grab_start   开始抢机\n"
        "/grab_stop    停止抢机\n"
        "/grab_status  查看状态")


def log(msg):
    line = "[%s] %s" % (time.strftime("%F %T"), msg)
    if TOKEN:
        line = line.replace(TOKEN, "***")  # 任何情况下都不把 token 写进日志
    print(line, flush=True)


def is_transient(e):
    """网络/代理导致的长轮询中断: 等几秒重来即可，不是故障。"""
    if isinstance(e, urllib.error.HTTPError):
        return False  # 带 HTTP 状态码的错误(如 401)不属于网络抖动
    return isinstance(e, TRANSIENT_ERRORS + (urllib.error.URLError,))


def is_auth_error(e):
    """token 失效/被撤销: 401 Unauthorized、403 Forbidden。"""
    return isinstance(e, urllib.error.HTTPError) and e.code in (401, 403)


def load_allowed_id():
    raw = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not raw.isdigit() or int(raw) <= 0:
        sys.exit("TELEGRAM_CHAT_ID 必须是你自己的数字用户 ID（正整数），拒绝启动")
    return int(raw)


ALLOWED_ID = load_allowed_id()


def single_instance():
    """只允许运行一份，否则两个进程同时拉取消息会互相冲突。"""
    others = runner.BOT.pids()  # 已自动排除自己/祖先，并把 caffeinate 派生的子进程去重
    if others:
        # 走 log()，让这行也带时间戳（以前 sys.exit 直接写 stderr，日志里是一行裸文本）
        log("已有另一个 tg_control 在运行 (pid %s)，本次启动退出"
            % ",".join(map(str, others)))
        sys.exit(1)


def is_authorized(msg):
    chat = msg.get("chat") or {}
    sender = msg.get("from") or {}
    return (
        chat.get("type") == "private"
        and chat.get("id") == ALLOWED_ID
        and sender.get("id") == ALLOWED_ID
        and not sender.get("is_bot", False)
    )


def parse_command(text):
    """消息必须恰好是一条白名单命令（可带 @机器人名 后缀）。
    多出任何内容（参数、分号、&& 等）都视为无效，不执行。"""
    parts = (text or "").split()
    if len(parts) != 1:
        return None
    cmd = parts[0].split("@", 1)[0].lower()
    return cmd if cmd in COMMANDS else None


def reply(api, text):
    try:
        api.send_message(ALLOWED_ID, text)  # 永远只回复给授权用户
    except Exception as e:
        log("发送回复失败: %r" % (e,))


def handle(api, msg):
    if not msg:
        return
    if not is_authorized(msg):
        sender = msg.get("from") or {}
        log("忽略未授权消息 (from id=%r)" % (sender.get("id"),))  # 不记录消息内容
        return
    if time.time() - msg.get("date", 0) > MAX_AGE_SEC:
        log("忽略过期消息")
        return
    cmd = parse_command(msg.get("text"))
    if cmd is None:
        reply(api, HELP)
        return
    log("执行 %s" % cmd)
    try:
        result = COMMANDS[cmd]()
    except Exception as e:
        log("%s 执行出错: %r" % (cmd, e))
        reply(api, "❌ 执行出错，详见 tg_control.log")
        return
    reply(api, runner.describe(result))  # 结构化结果 → 带 emoji 的纯文本


def main():
    if not TOKEN:
        sys.exit("缺少 TELEGRAM_BOT_TOKEN")
    single_instance()
    api = TelegramApi(TOKEN)

    # 启动时先丢弃积压的消息，防止重放离线期间堆积的旧命令
    offset = None
    try:
        pending = api.get_updates(offset=-1, poll_timeout=0)
        if pending:
            offset = pending[-1]["update_id"] + 1
    except Exception as e:
        log("清理积压消息失败: %r" % (e,))

    log("遥控已启动，授权用户 ID: %s" % ALLOWED_ID)
    reply(api, "🤖 抢机遥控已上线\n\n" + HELP)
    try:
        api.set_menu(ALLOWED_ID, MENU)  # 菜单只对授权用户可见
    except Exception as e:
        log("注册菜单按钮失败（不影响命令使用）: %r" % (e,))

    backoff = 5
    transient = 0
    while True:
        try:
            updates = api.get_updates(offset=offset)
        except Exception as e:
            if is_transient(e):
                # 代理/网络把长轮询连接掐断属于正常现象: 安静重试，别刷"错误"
                transient += 1
                if transient == 1 or transient % 10 == 0:
                    log("长轮询连接被中断 (%s)，属于代理/网络的正常现象，"
                        "%s 秒后继续（连续第 %d 次）"
                        % (type(e).__name__, backoff, transient))
                time.sleep(backoff)
                backoff = min(backoff * 2, 30)  # 连接抖动用不着退避太久
            else:
                if is_auth_error(e):
                    log("拉取消息失败: %s。TELEGRAM_BOT_TOKEN 可能已失效或被 /revoke，"
                        "请在 config.env 更新后执行 ./ctl.sh bot-restart"
                        % (e,))
                else:
                    log("拉取消息失败: %r，%s 秒后重试" % (e, backoff))
                time.sleep(backoff)
                backoff = min(backoff * 2, 120)  # 别像以前那样一退就是 5 分钟
            continue
        if transient:
            # 恢复行里的次数是真实中断次数（中间几次没有逐条记日志，见上面），
            # 措辞上写明"期间共中断 N 次"，免得读者以为漏了日志
            log("长轮询已恢复正常（期间共中断 %d 次，已自动重试）" % transient)
            transient = 0
        backoff = 5
        for update in updates:
            offset = update["update_id"] + 1
            try:
                handle(api, update.get("message"))
            except Exception as e:  # 单条消息出错不能让整个进程退出
                log("处理消息出错: %r" % (e,))


if __name__ == "__main__":
    main()
