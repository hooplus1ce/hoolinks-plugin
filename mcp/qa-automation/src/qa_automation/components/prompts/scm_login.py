"""SCM 登录操作指令 prompt：客户端可直接获取标准登录话术，无需手打。"""
from __future__ import annotations

from fastmcp.prompts import prompt

from qa_automation.config import SCM_BASE_URL

# 登录后目标页面：由 .env SCM_BASE_URL 派生（未配置则为空，客户端应传入 base_url）。
DEFAULT_BASE_URL = (SCM_BASE_URL.rstrip("/") + "/static/admin") if SCM_BASE_URL else ""


@prompt(
    name="scm-login",
    title="SCM Login: Browser Login Guide",
    description="生成在已打开浏览器中完成 SCM 登录并进入工作台的完整操作指令。",
    tags={"login", "scm", "browser"},
)
def scm_login(session: str = "scm", base_url: str = DEFAULT_BASE_URL) -> str:
    """生成 SCM 登录操作指令。

    Args:
        session: 会话名（默认 scm）。
        base_url: 登录后要打开的目标页面。
    """
    return f"""请按以下步骤在已打开的浏览器中完成 SCM 系统登录并进入工作台：

1. 调用 browser_connect 连接 http://127.0.0.1:9222 的浏览器（接管已打开的窗口，不要自启新浏览器）。
2. 调用 session_create 创建会话 "{session}"（默认在可见窗口操作，不要传 use_default=false）。
3. 调用 login_with_captcha 登录会话 "{session}"：
   - 账号密码从环境变量自动读取（SCM_USERNAME / SCM_USERPWD / SCM_BASE_URL），不要向用户询问；
   - 验证码由工具自动 OCR 识别；若返回验证码类错误，直接重试（工具会自动刷新验证码）。
4. 调用 page_goto 打开 {base_url}，等待页面加载。
5. 确认已登录：页面应显示业务菜单（采购管理、库存管理等）和当前用户名；
   若停留在登录页，检查上一步错误信息并重试。
6. 登录成功后调用 session_save_state 保存账号态（默认 .auth/{session}.json），供后续免验证码复用。

约束：全程不要关闭或重启用户的浏览器；所有操作应发生在用户可见的窗口内。"""
