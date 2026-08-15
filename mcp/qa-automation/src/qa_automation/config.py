"""集中配置：项目根解析、统一超时/重试参数、目录常量与路径锚定。

对齐 qa-automation-plugin 的 config.py 思路，适配本扁平项目结构。
所有超时/重试/目录均可用环境变量覆盖：慢环境整体放大时改一处即可，无需逐文件改魔法数字。

串行锁决策（记录在案，供后续维护参考）：
- 本服务 transport=stdio、单客户端、单 agent，工具按序调用（上一工具返回后才
  发起下一个）；宿主将「并行」需求 route 到 task 子代理，而非并行 MCP 工具调用。
- 实测 FastMCP 3.4.x 工具执行路径无全局串行化（唯一 asyncio.Lock 在 OAuth 代理
  的 token 刷新，与工具调用无关）；但并发工具调用仅在 HTTP 多客户端/并行调用下
  才发生，当前部署形态不会触发。
- 故共享 PlaywrightLifecycle 当前无并发写入风险，全局串行锁是纯开销（序列化
  全部工具 + 加时延），不引入。若未来切 HTTP 多客户端，应在 PlaywrightLifecycle
  内加 per-lifecycle asyncio.Lock 包住浏览器变更类工具，而非全局串行中间件。
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# ---- 根解析：项目根 / 插件根 / 数据根 ----
# src 布局：config.py 位于 src/qa_automation/，项目根（pyproject.toml、.env）在其
# 上两级。先加载项目根 .env（已存在的环境变量不覆盖），使本模块常量在任何导入顺序
# 下都能读到 .env 值。
_SOURCE_DIR = Path(__file__).resolve().parent  # src/qa_automation
PROJECT_ROOT = _SOURCE_DIR.parent.parent  # mcp/qa-automation（uv 项目根）


def _find_plugin_root() -> Path:
    """沿目录树向上找 plugin.json，即 Agent Plugins 插件根（skills/ 所在）。"""
    for parent in (_SOURCE_DIR, *_SOURCE_DIR.parents):
        if (parent / "plugin.json").is_file():
            return parent
    return PROJECT_ROOT


# Agent Plugins 一致性客户端注入 PLUGIN_ROOT；开发直跑时回退为向上搜索到的插件根。
PLUGIN_ROOT = Path(os.environ.get("PLUGIN_ROOT", _find_plugin_root()))
load_dotenv(PROJECT_ROOT / ".env", override=False)
# 服务私有数据根（accounts.json / .auth）：固定在 MCP 项目目录（mcp/qa-automation），
# 不随插件包（PLUGIN_ROOT）或使用方项目变化；PROJECT_DIR 可显式覆盖（绝对路径）。
PROJECT_DIR = Path(os.environ.get("PROJECT_DIR", PROJECT_ROOT))
# 资产根：使用该插件的项目目录（截图/导出用例/下载/证据等生成文件）。
# 由使用方注入 WORK_DIR（Agent 客户端环境变量或 mcp.json env）；未配置回退 PROJECT_DIR。
WORK_DIR = Path(os.environ.get("WORK_DIR", PROJECT_DIR))


def project_path(path: str) -> str:
    """将相对路径锚定到资产根 WORK_DIR（绝对路径/空串原样返回，~ 展开为用户目录）。"""
    if not path:
        return str(WORK_DIR)
    expanded = os.path.expanduser(path)
    if os.path.isabs(expanded):
        return expanded
    return str(WORK_DIR / expanded)


# ---- 目录常量 ----
# 凭据/登录态（服务私有）→ PROJECT_DIR（mcp/qa-automation）
AUTH_DIR = PROJECT_DIR / ".auth"
# 生成资产（截图/导出/下载/证据）→ WORK_DIR（使用该插件的项目目录）
ARTIFACTS_DIR = WORK_DIR / "artifacts"
SCREENSHOT_DIR = ARTIFACTS_DIR / "screenshots"
DOWNLOAD_DIR = Path(project_path(os.getenv("DOWNLOAD_DIR", "downloads")))
OUTPUT_DIR = Path(project_path(os.getenv("OUTPUT_DIR", "output_testcases")))
EVIDENCE_DIR = Path(project_path(os.getenv("EVIDENCE_DIR", "evidence_assets")))


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# ---- 连接 ----
CDP_URL = os.getenv("CDP_URL", "http://127.0.0.1:9222")
CONNECT_RETRY_ATTEMPTS = _env_int("CONNECT_RETRY_ATTEMPTS", 3)
CONNECT_RETRY_BACKOFF_MS = _env_int("CONNECT_RETRY_BACKOFF_MS", 1500)

# ---- 登录 / 视觉 ----
# 被测系统根地址：必填，经 .env SCM_BASE_URL 配置（或 accounts.json base_url）。
# 不提供默认域名——不同部署环境的系统地址由部署方注入，避免业务域名写死在代码。
SCM_BASE_URL = os.getenv("SCM_BASE_URL", "")
# 虚拟光标/目标高亮服务级默认开关（工具未显式传 visualize 时生效）
VISUAL_EFFECTS = _env_bool("VISUAL_CURSOR_ENABLED", False)

# ---- 时序/重试（统一调优成功率）----
ELEMENT_WAIT_TIMEOUT_MS = _env_int("ELEMENT_WAIT_TIMEOUT_MS", 6000)
ACTION_RETRY_ATTEMPTS = _env_int("ACTION_RETRY_ATTEMPTS", 3)
ACTION_RETRY_BACKOFF_MS = _env_int("ACTION_RETRY_BACKOFF_MS", 500)
OBSERVE_WAIT_MS = _env_int("OBSERVE_WAIT_MS", 1500)
TOOL_MAX_EXECUTION_MS = _env_int("TOOL_MAX_EXECUTION_MS", 300000)
INTERACT_TIMEOUT_S = _env_int("INTERACT_TIMEOUT_S", 10)
