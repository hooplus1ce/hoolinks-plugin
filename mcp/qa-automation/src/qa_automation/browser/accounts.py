"""多账号凭据管理（accounts.json）。

结构::

    {
      "base_url": "https://<your-scm-host>",
      "accounts": {
        "admin":    {"username": "...", "password": "..."},
        "operator": {"username": "...", "password": "..."}
      }
    }

密码仅存于本文件（已 gitignore），工具调用/对话中不返回密码。
"""
from __future__ import annotations

import json
from typing import Any

from qa_automation.config import PROJECT_DIR

# accounts.json 是服务私有凭据数据，固定在 MCP 项目目录（config.PROJECT_DIR，
# 默认 mcp/qa-automation，可由 PROJECT_DIR 环境变量显式覆盖）。
ACCOUNTS_FILE = PROJECT_DIR / "accounts.json"


def _load_raw() -> dict[str, Any]:
    try:
        data = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def load_accounts() -> dict[str, Any]:
    """返回 {base_url, accounts: {name: {username, password}}}；文件缺失返回空结构。"""
    data = _load_raw()
    accounts = data.get("accounts")
    if not isinstance(accounts, dict):
        accounts = {}
    return {"base_url": data.get("base_url", ""), "accounts": accounts}


def save_account(
    name: str,
    username: str,
    password: str,
    base_url: str | None = None,
) -> None:
    """新增或更新账号（写入 accounts.json）。"""
    data = _load_raw()
    accounts = data.setdefault("accounts", {})
    accounts[name] = {"username": username, "password": password}
    if base_url:
        data["base_url"] = base_url
    ACCOUNTS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def remove_account(name: str) -> bool:
    """删除账号；不存在返回 False。"""
    data = _load_raw()
    accounts = data.get("accounts")
    if not isinstance(accounts, dict) or name not in accounts:
        return False
    del accounts[name]
    ACCOUNTS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return True


def get_account(name: str) -> tuple[str, str] | None:
    """按账号名返回 (username, password)；不存在返回 None。"""
    cred = load_accounts()["accounts"].get(name)
    if cred and cred.get("username") and cred.get("password"):
        return cred["username"], cred["password"]
    return None


def resolve(
    account: str, username: str | None, password: str | None
) -> tuple[str, str] | None:
    """解析登录凭据：account 名（accounts.json）优先，否则显式 username/password。

    Returns:
        (username, password)；无法解析返回 None。
    """
    if account:
        cred = get_account(account)
        if cred is None:
            return None
        return username or cred[0], password or cred[1]
    if username and password:
        return username, password
    return None
