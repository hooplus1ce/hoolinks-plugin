"""多账号管理 MCP 工具：账号增删查（凭据存 accounts.json，密码不返回）。"""
from __future__ import annotations

from fastmcp import Context
from fastmcp.tools import tool
from mcp.types import Icon

from qa_automation.browser import accounts

_ACCOUNT_ICON = Icon(
    src="data:image/svg+xml;base64,PHN2ZyB4bWxucz0naHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmcnIHdpZHRoPScyNCcgaGVpZ2h0PScyNCc+PGNpcmNsZSBjeD0nMTInIGN5PSc4JyByPSc0JyBmaWxsPSdub25lJyBzdHJva2U9JyUyMzEwYjkyNicgc3Ryb2tlLXdpZHRoPScyJy8+PHBhdGggZD0nTTQgMjBjMC0zLjUgMy41LTUgOC01czggMS41IDggNScgZmlsbD0nbm9uZScgc3Ryb2tlPSclMjMxMGI5MjYnIHN0cm9rZS13aWR0aD0nMicvPjwvc3ZnPg==",
    mime_type="image/svg+xml",
)


@tool(
    title="Account: Add",
    description="新增/更新账号到 accounts.json（多账号管理）。密码仅写入本地文件，不会在返回结果中出现。"
    "登录时用 account=<账号名> 引用。",
    icons=[_ACCOUNT_ICON],
    tags={"account", "auth"},
)
async def account_add(
    ctx: Context,
    name: str,
    username: str,
    password: str,
    base_url: str | None = None,
) -> dict:
    """新增或更新账号。

    Args:
        name: 账号名（登录时 account=<name> 引用）。
        username: 登录账号。
        password: 登录密码（仅写入本地 accounts.json）。
        base_url: 系统根地址（首次配置时填写；缺省沿用已有值）。
    """
    accounts.save_account(name, username, password, base_url)
    await ctx.info(f"account {name!r} saved to accounts.json")
    return {"ok": True, "name": name, "username": username}


@tool(
    title="Account: List",
    description="列出 accounts.json 中已配置的账号（只显示账号名与用户名，不显示密码）。",
    icons=[_ACCOUNT_ICON],
    tags={"account"},
)
async def account_list(ctx: Context) -> dict:
    """列出全部账号。"""
    data = accounts.load_accounts()
    items = [
        {"name": n, "username": c.get("username", "")}
        for n, c in data["accounts"].items()
    ]
    return {"ok": True, "base_url": data["base_url"], "accounts": items}


@tool(
    title="Account: Remove",
    description="从 accounts.json 删除指定账号。",
    icons=[_ACCOUNT_ICON],
    tags={"account"},
)
async def account_remove(ctx: Context, name: str) -> dict:
    """删除账号。

    Args:
        name: 账号名。
    """
    removed = accounts.remove_account(name)
    if not removed:
        return {"ok": False, "error": f"account {name!r} not found"}
    await ctx.info(f"account {name!r} removed")
    return {"ok": True, "removed": name}
