"""Telegram Bot API 的最小封装，只用标准库，不依赖第三方包。"""
import json
import os
import urllib.parse
import urllib.request

# 默认走官方地址；TELEGRAM_API_BASE 仅用于本地测试时指向模拟服务器
API_BASE = os.environ.get("TELEGRAM_API_BASE", "https://api.telegram.org")


class TelegramApi:
    def __init__(self, token):
        self._base = "%s/bot%s" % (API_BASE, token)

    def _call(self, method, params, timeout):
        data = urllib.parse.urlencode(params).encode()
        req = urllib.request.Request("%s/%s" % (self._base, method), data=data)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.load(resp)
        if not body.get("ok"):
            raise RuntimeError("%s 失败: %s" % (method, body.get("description")))
        return body["result"]

    def get_updates(self, offset=None, poll_timeout=30):
        """长轮询拉取消息。只订阅普通消息，不接收其他类型的更新。"""
        params = {
            "timeout": poll_timeout,
            "allowed_updates": json.dumps(["message"]),
        }
        if offset is not None:
            params["offset"] = offset
        return self._call("getUpdates", params, timeout=poll_timeout + 10)

    def set_menu(self, chat_id, commands):
        """给指定的私聊注册命令菜单。scope 限定为这一个聊天，所以别人看不到这些命令。"""
        self._call("setMyCommands", {
            "commands": json.dumps(
                [{"command": c, "description": d} for c, d in commands]),
            "scope": json.dumps({"type": "chat", "chat_id": chat_id}),
        }, timeout=15)
        self._call("setChatMenuButton", {
            "chat_id": chat_id,
            "menu_button": json.dumps({"type": "commands"}),
        }, timeout=15)

    def send_message(self, chat_id, text):
        return self._call(
            "sendMessage", {"chat_id": chat_id, "text": text[:4000]}, timeout=15
        )
