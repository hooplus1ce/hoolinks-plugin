# hoolinks-plugin — qa-automation

生和堂 APS/SCM/WMS 企业级 UI 自动化测试 Agent 插件，符合 [Agent Plugins 1.0.0](https://agent-plugins.org/specification) 打包规范：可移植的 **Agent Skills**（测试设计矩阵 / 执行与回归）+ **MCP 服务器**（FastMCP，CDP 浏览器接管、验证码/视觉识别、VTable 表格、用例录制导出）。

## 目录结构

```
├── plugin.json          # Agent Plugins 1.0.0 插件清单（$schema + name + 元数据）
├── mcp.json             # stdio MCP 服务器声明（uv run fastmcp run fastmcp.json）
├── skills/              # 固定位置：Agent Skills（qa-automation-guide / ui-automation-test）
├── accounts.json.example# 多账号凭据模板（复制为 accounts.json 后填写）
├── scripts/validate.py  # 插件一致性检查（零依赖）
└── mcp/                 # uv workspace（多 MCP 项目共享一个 .venv）
    └── qa-automation/   # 本项目 MCP 服务（FastMCP 3.x，Python >=3.14）
        ├── pyproject.toml / uv.lock / .python-version
        ├── fastmcp.json          # FastMCP 声明式服务器配置
        ├── .env.example          # 环境变量模板（复制为 .env 后填写）
        └── src/qa_automation/    # server.py / config.py / browser/ / components/
```

## 安装（Agent 平台）

Agent Plugins 兼容客户端以 git URL 添加插件：

```
https://github.com/hooplus1ce/hoolinks-plugin.git
```

客户端克隆后即可发现 `skills/` 中的技能并启动 `mcp.json` 声明的 stdio 服务器（一致性客户端自动注入 `PLUGIN_ROOT`/`PLUGIN_DATA` 并展开 `${PLUGIN_ROOT}`）。

## 配置

所有环境相关的值一律通过配置文件注入，代码中不含业务域名硬编码。三个配置文件均**不上传真实内容**，仓库只提供模板：

### 1. `.env`（必填）

将 `mcp/qa-automation/.env.example` 复制为 `mcp/qa-automation/.env` 并填写。关键项：

| 变量 | 必填 | 说明 |
|---|---|---|
| `SCM_BASE_URL` | **是** | 被测系统根地址（登录接口、验证码、工作台基于此拼接） |
| `FASTMCP_LOG_LEVEL` / `FASTMCP_TRANSPORT` | 否 | 日志级别 / 传输方式（默认 stdio） |
| `CDP_URL` | 否 | 浏览器接管端点（默认 `http://127.0.0.1:9222`） |
| `VISUAL_CURSOR_ENABLED` | 否 | 虚拟光标/目标高亮默认开关 |
| `PROJECT_DIR` | 否 | 数据根覆盖（默认插件根；插件环境由 mcp.json 注入 `${PLUGIN_ROOT}`） |

### 2. `accounts.json`（可选，多账号）

将 `accounts.json.example` 复制为插件根 `accounts.json` 并填写。结构：

```json
{
  "base_url": "https://your-scm-host.example.com",
  "accounts": {
    "admin": { "username": "…", "password": "…" }
  }
}
```

也可在运行时用 `account_add` / `account_list` / `account_remove` 工具管理。文件已 gitignore，密码不返回给模型。

### 3. `.auth/`（运行时自动生成，无需手动配置）

`.auth/` 存放程序生成的**登录态**（OAuth token、cookie 快照，如 `session_save_state` 的落盘、Antigravity 视觉授权凭据）。该目录由工具自动写入/读取，**请勿手动编辑或提交**，已 gitignore。

> **为什么 `accounts.json` 不放进 `.auth/`**：`accounts.json` 是用户维护的**输入配置**（账号/密码，静态、由人管理），`.auth/` 是程序产生的**运行状态**（会话/令牌，动态、由工具生成）。二者生命周期不同——清空登录态重登时不应误删账号配置，反之亦然——故保持分离。统一收纳会带来"清理状态连带删除配置"的风险。

## 本地开发

```bash
cd mcp                      # uv workspace 根
uv sync                     # 安装全部成员（共享 .venv）
uv run --project qa-automation pytest -q   # 测试
uv run python ../scripts/validate.py       # 插件一致性检查（在仓库根）
cd qa-automation && uv run fastmcp run fastmcp.json   # 直接启动（stdio）
```

## 安全边界

- `.env`（真实密钥）、`accounts.json`（账号密码）、`.auth/`（登录态）、`artifacts/`（截图）均被 `.gitignore` 排除，仓库内无凭据
- 登录接口路径/字段名等被测系统适配参数集中在 `src/qa_automation/browser/login.py` 的 `SCM_LOGIN_CONFIG`，不同系统按实际调整
