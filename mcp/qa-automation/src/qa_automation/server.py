from pathlib import Path

from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.server.lifespan import lifespan
from fastmcp.server.providers import FileSystemProvider
from fastmcp.server.providers.skills import SkillsDirectoryProvider

from qa_automation.browser.lifecycle import PlaywrightLifecycle
from qa_automation.config import CDP_URL, PLUGIN_ROOT

load_dotenv()

# 服务器级 instructions：握手时注入客户端（MCP 协议标准），约束 AI 直接调用工具
SERVER_INSTRUCTIONS = """你是 APS/SCM/WMS 企业级 UI 自动化测试 MCP 服务器。所有能力均通过下方工具直接暴露，调用工具即可完成任务——不要读取项目源码、不要猜测实现、不要拆解工具已封装好的流程。

关键工具速查：
- browser_connect: 接管 9222 已打开的浏览器并自动完成会话初始化（一步完成连接+创建激活会话+接管前台激活标签页）
- session_create: 显式新建命名会话（默认单会话已被 browser_connect 自动完成；多账号隔离测试传 use_default=false）
- login_with_captcha: SCM 完整登录（自动请求验证码→视觉识别→登录→cookies 注入→跳转工作台），账号密码从环境变量/accounts.json 读取
- captcha_recognize / vision_recognize: 视觉识别（验证码/通用图片）
- page_goto / page_click / page_fill / page_screenshot: 页面操作
- session_save_state: 登录态落盘复用
- download_file / upload_file: 文件下载（CDP 重定向，xlsx 自动预览）与上传（filechooser 拦截）
- vtable_*: VTable canvas 表格取数/勾选/拖拽（APS 排产表格场景，canvas 无 DOM 复选框）
- wait_for_condition: 页面条件轮询等待（元素可见/文本出现/URL 跳转）
- start_recording / execute_and_record / export_session: 用例录制与导出（JSON + Excel）
- skills: 测试设计 SOP（qa-automation-guide 用例矩阵、ui-automation-test 执行/回归），设计用例时自动调用

使用原则：
1. 连接浏览器原则：用户要求连接浏览器时，除非指明要连接别的端口的浏览器（如 cdp_url=...）或开启多个不同账号下的上下文环境隔离的浏览器（如 session_open_isolated / use_default=false），否则直接调用 browser_connect 即可默认连接 9222 端口并自动创建/激活默认会话，一次性完成前置初始化。
2. 连接完成后会话已自动激活，后续页面操作（page_goto / page_interact / login_with_captcha / analyze_current_page）直接执行，缺省自动作用于当前激活会话，无需再单独调用 session_create。
3. login_with_captcha 已封装完整登录流程，直接调用即可，不要拆成手动步骤。
4. 视觉识别为多模态模型（gemini-3.6-flash），验证码识别已默认低思考强度。
5. 浏览器生命周期由服务器管理，操作结束前不要关闭/断开浏览器连接。
6. 多账号/多窗口场景：页面操作（page_*）必须显式传 session 参数；每次操作前先调用 session_list 确认会话与账号的对应关系（account 字段），严禁操作落到其他账号的窗口。
7. 标签页策略：优先接管/切换浏览器中已存在的标签页（page_* 自动接管前台激活标签页；目标页在后台时用 tab_list 查看、tab_switch 切换），严禁新建空白标签页后再操作。
8. 元素交互统一用 page_interact：定位信息（role/name、text、placeholder、css、视口坐标 x/y）优先取自 analyze_current_page 输出；坐标模式不做计算直接使用传入 x/y；in_iframe 默认 true（激活 iframe 内查找）；禁用截图→坐标的视觉驱动方案。"""
@lifespan
async def app_lifespan(server):
    """服务器生命周期：启动时创建浏览器生命周期管理器，停止时优雅关闭。

    lifespan 上下文注入工具：{"lifecycle": PlaywrightLifecycle}。
    视觉识别（Antigravity gemini-3.6-flash）无本地状态，工具直接调用 browser.vision。
    接管目标默认 http://127.0.0.1:9222，可用环境变量 CDP_URL 覆盖（如 CDP_URL=http://127.0.0.1:9333）。
    """
    lifecycle = PlaywrightLifecycle(
        cdp_url=CDP_URL
    )
    try:
        yield {"lifecycle": lifecycle}
    finally:
        await lifecycle.close()


mcp = FastMCP(
    "QA MCP Server",
    instructions=SERVER_INSTRUCTIONS,
    lifespan=app_lifespan,
    providers=[
        FileSystemProvider(Path(__file__).parent / "components"),
        SkillsDirectoryProvider(roots=PLUGIN_ROOT / "skills"),
    ],
)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
