"""accounts.json 多账号管理测试（tmp 文件隔离，不碰真实凭据）。"""
from __future__ import annotations

import pytest
from fastmcp import Client

from qa_automation.browser import accounts
from qa_automation.server import mcp


@pytest.fixture
def acct_file(tmp_path, monkeypatch) -> str:
    f = tmp_path / "accounts.json"
    monkeypatch.setattr(accounts, "ACCOUNTS_FILE", f)
    return str(f)


def test_save_and_load(acct_file: str) -> None:
    accounts.save_account("admin", "u1", "p1", "https://scm.example.com")
    accounts.save_account("operator", "u2", "p2")
    data = accounts.load_accounts()
    assert data["base_url"] == "https://scm.example.com"
    assert set(data["accounts"]) == {"admin", "operator"}
    assert data["accounts"]["operator"] == {"username": "u2", "password": "p2"}


def test_get_and_remove(acct_file: str) -> None:
    accounts.save_account("admin", "u1", "p1")
    assert accounts.get_account("admin") == ("u1", "p1")
    assert accounts.get_account("nope") is None
    assert accounts.remove_account("admin") is True
    assert accounts.remove_account("admin") is False
    assert accounts.get_account("admin") is None


def test_missing_file_is_empty(acct_file: str) -> None:
    data = accounts.load_accounts()
    assert data == {"base_url": "", "accounts": {}}


@pytest.fixture
async def client() -> Client:
    async with Client(transport=mcp) as c:
        yield c


async def test_account_tools_roundtrip(client: Client, acct_file: str) -> None:
    r = await client.call_tool(
        "account_add",
        {"name": "tester", "username": "tu", "password": "tp"},
    )
    assert r.data["ok"] is True

    r = await client.call_tool("account_list", {})
    assert r.data["ok"] is True
    by_name = {a["name"]: a for a in r.data["accounts"]}
    assert by_name["tester"]["username"] == "tu"
    # 密码不返回
    assert "password" not in by_name["tester"]

    r = await client.call_tool("account_remove", {"name": "tester"})
    assert r.data["ok"] is True
    r = await client.call_tool("account_remove", {"name": "tester"})
    assert r.data["ok"] is False  # 已删除
