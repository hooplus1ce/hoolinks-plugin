# qa-automation — Agent Plugin

企业级 Web 系统 UI 自动化测试插件，符合 [Agent Plugins 1.0.0](https://agent-plugins.org/specification) 打包规范：可移植的 **Agent Skills**（测试设计矩阵 / 执行与回归）+ **MCP 服务器**（FastMCP，CDP 浏览器接管、验证码/视觉识别、VTable 表格、用例录制导出）。

被测系统域名、账号等环境相关值一律通过配置文件注入，代码与仓库内不含任何业务域名硬编码。

## 目录结构

```
├── plugin.json          # Agent Plugins 1.0.0 插件清单（$schema + name + 元数据）
├── mcp.json             # stdio MCP 服务器声明（uv run fastmcp run fastmcp.json）
├── skills/              # 固定位置：Agent Skills（qa-automation-guide / ui-automation-test）
├── scripts/validate.py  # 插件一致性检查（零依赖）
└── mcp/                 # uv workspace（多 MCP 项目共享一个 .venv）
    └── qa-automation/   # 本项目 MCP 服务（FastMCP 3.x，Python >=3.14）
        ├── pyproject.toml / uv.lock / .python-version
        ├── fastmcp.json           # FastMCP 声明式服务器配置
        ├── .env.example           # 环境变量模板（复制为 .env 后填写）
        ├── accounts.json.example  # 多账号凭据模板（复制为 accounts.json 后填写）
        ├── accounts.json          # 多账号凭据（运行时数据，gitignore）
        ├── .auth/                 # 登录态（OAuth/cookie，运行时生成，gitignore）
        ├── artifacts/             # 本地开发默认资产目录（生产由 WORK_DIR 指向使用方项目）
        └── src/qa_automation/     # server.py / config.py / browser/ / components/
```

## 安装（Agent 平台）

Agent Plugins 兼容客户端以 git URL 添加插件：

```
https://github.com/hooplus1ce/hoolinks-plugin.git
```

客户端克隆后即可发现 `skills/` 中的技能并启动 `mcp.json` 声明的 stdio 服务器（一致性客户端自动注入 `PLUGIN_ROOT`/`PLUGIN_DATA` 并展开 `${PLUGIN_ROOT}`）。

### 远程 MCP 服务（tencent-docs）

`mcp.json` 还声明了一个远程 Streamable HTTP 服务 `tencent-docs`（`https://docs.qq.com/openapi/mcp`）。按 Agent Plugins 规范，**认证凭据不写入插件配置**（headers 是可见包数据且不做占位符展开），授权由客户端在连接时管理：

- 在 Agent 客户端的 per-server 授权/凭据配置中为 `tencent-docs` 提供 token，客户端连接时注入 `Authorization`
- FastMCP 客户端可显式使用 `StreamableHttpTransport(url=..., auth=BearerAuth("<token>"))`
- 未配置授权时该服务**连接失败不影响其他服务**（§7.2.2：独立组件失败非致命），`qa-automation` 照常可用

## 配置

### 必填环境变量

只有一个**必填**项：

| 变量 | 必填 | 说明 |
|---|---|---|
| `SCM_BASE_URL` | **是** | 被测系统根地址。登录接口、验证码接口、工作台页面均基于此拼接。**未配置时登录/验证码类工具会返回明确错误** |

配置位置：`mcp/qa-automation/.env`（复制 `.env.example` 后填写）。

### 可选环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `CDP_URL` | `http://127.0.0.1:9222` | `browser_connect` 接管/自启的浏览器调试端点 |
| `VISUAL_CURSOR_ENABLED` | `false` | 虚拟光标/目标高亮服务级默认开关 |
| `PROJECT_DIR` | `mcp/qa-automation` | 服务私有数据根（accounts.json / .auth），一般无需修改 |
| `WORK_DIR` | `${PLUGIN_DATA}`（mcp.json 注入） | **资产根**：截图/导出用例/下载/证据等生成文件的落盘目录，指向"使用该插件的项目目录" |
| `DOWNLOAD_DIR` | `downloads` | `download_file` 默认保存目录（相对 WORK_DIR） |
| `OUTPUT_DIR` | `output_testcases` | `export_session` 导出用例 JSON/Excel 目录（相对 WORK_DIR） |
| `EVIDENCE_DIR` | `evidence_assets` | 证据/截图目录（相对 WORK_DIR） |
| `FASTMCP_LOG_LEVEL` / `FASTMCP_TRANSPORT` | `INFO` / `stdio` | FastMCP 自身日志级别与传输方式 |
| `ELEMENT_WAIT_TIMEOUT_MS` 等 | 见 `.env.example` | 时序/重试调优（慢环境整体放大） |

### 多账号（可选）：`accounts.json`

将 `mcp/qa-automation/accounts.json.example` 复制为 `mcp/qa-automation/accounts.json` 并填写：

```json
{
  "base_url": "https://your-scm-host.example.com",
  "accounts": {
    "admin": { "username": "…", "password": "…" }
  }
}
```

也可运行时用 `account_add` / `account_list` / `account_remove` 工具管理。文件已 gitignore，密码不返回给模型。

### 登录态：`.auth/`（自动生成，无需配置）

`mcp/qa-automation/.auth/` 由工具自动写入/读取（`session_save_state` 落盘等会话登录态）。请勿手动编辑或提交。

> Antigravity 视觉识别的 OAuth 凭据**不在 `.auth/`**：默认存于用户主目录 `~/.qa-automation-plugin/antigravity-credentials.json`（可用 `ANTIGRAVITY_CREDENTIALS_FILE` 覆盖路径），client 凭证（`ANTIGRAVITY_CLIENT_ID`/`ANTIGRAVITY_CLIENT_SECRET`）读取自 `~/.qa-automation-plugin/.env`。

> `accounts.json` 与 `.auth/` 分目录但同处：前者是用户维护的**输入配置**（静态账号），后者是程序产生的**运行状态**（动态会话）。生命周期不同——清登录态重登不应误删账号配置——故保持分离。

### 数据与资产分层

| 类别 | 位置 | 说明 |
|---|---|---|
| 凭据/登录态 | `mcp/qa-automation/`（accounts.json + .auth/） | 服务私有，固定在 MCP 项目目录 |
| 生成资产 | `WORK_DIR`（默认 `${PLUGIN_DATA}`） | 截图/导出用例/下载/证据，保存到使用该插件的项目目录 |

## 使用示例

以下以一个真实场景说明：**对某 Web 系统（假设根地址 `https://scm.example.com`）的排产页面做一次 UI 自动化回归，产出用例与证据资产**。

### 第 1 步：环境准备

```bash
# mcp/qa-automation/.env（必填）
SCM_BASE_URL=https://scm.example.com

# mcp/qa-automation/accounts.json（可选，多账号）
{"base_url": "https://scm.example.com", "accounts": {"admin": {"username": "u_admin", "password": "***"}}}
```

浏览器以调试模式启动（`chrome --remote-debugging-port=9222`），或交给服务器自启。

### 第 2 步：在 Agent 客户端中添加插件

将 `https://github.com/hooplus1ce/hoolinks-plugin.git` 添加为插件。客户端完成握手后，插件暴露 46 个 MCP 工具与 2 个 Agent Skill。

### 第 3 步：向 Agent 下达测试指令

> 用户：请对 scm.example.com 的"排产计划"页面执行一次登录回归测试，记录证据，导出用例报表。

### 第 4 步：Agent 自动编排的工具链（实际调用序列）

1. `browser_connect`（mode=auto）——接管 9222 已打开的浏览器
2. `session_create`（name="reg"）——建会话
3. `login_with_captcha`（account="admin"）——从 accounts.json 取凭据 → 自动请求验证码 → OCR 识别 → 登录 → cookies 注入 → 跳转工作台
4. `analyze_current_page`——解析页面元素与 iframe 路径，拿到语义定位信息
5. `page_interact` / `vtable_get_cell_text` / `vtable_get_column_values`——在排产表格上按行/列取数断言（VTable canvas 场景）
6. `session_save_state`——登录态落盘 `.auth/reg.json`，下次免验证码复用
7. `export_session`——步骤级证据 JSON 落盘 `WORK_DIR/evidence_assets/`，Excel 用例报表落盘 `WORK_DIR/output_testcases/`

### 第 5 步：产出物

```
<WORK_DIR>/evidence_assets/排产_回归/排产_回归_asset.json   # 步骤证据，可复用驱动下次回归
<WORK_DIR>/output_testcases/排产_回归.xlsx                  # 可执行用例库/交付物
<WORK_DIR>/artifacts/screenshots/…                         # 页面截图
```

下次回归直接复用 `evidence_assets` 中的定位器与步骤，无需重新录制。

## 本地开发

```bash
cd mcp                      # uv workspace 根
uv sync                     # 安装全部成员（共享 .venv）
uv run --project qa-automation pytest -q   # 测试
uv run python ../scripts/validate.py       # 插件一致性检查（在仓库根）
cd qa-automation && uv run fastmcp run fastmcp.json   # 直接启动（stdio）
```

## 安全边界

- `.env`（真实密钥）、`accounts.json`（账号密码）、`.auth/`（登录态）、`artifacts/`（截图）均被 `.gitignore` 排除，仓库内无凭据、无业务域名
- 被测系统登录接口路径/字段名等适配参数集中在 `src/qa_automation/browser/login.py` 的 `SCM_LOGIN_CONFIG`，不同系统按实际调整
