# UI execution and regression

> 本流程用于用户显式要求的 UI 自动化录制、单条执行和回归复验。真实浏览器、账号和业务数据均按用户提供参数执行。

## 前置契约

1. 确认 Chrome 已以 `--remote-debugging-port=9222`（或用户指定端口）启动并已接管；
2. 收集系统 URL、目标菜单/页面、测试用例文档/子表、过滤字段和值、执行人、证据根目录、报告路径和回归模式；
3. 用 `tool_search` 搜索并阅读当前 qa-automation 工具 schema，至少覆盖页面动作、截图、弹层、会话和 VTable；
4. 用 `get_session_info`/`ui_page_context`/`analyze_current_page`（以实时工具为准）确认激活 Tab、完整 iframe `frame_path`、Portal 弹层和 UI 框架；
5. 用例文档字段至少映射：用例编号、用例名称、前置条件、测试步骤、测试数据、预期结果。

## 资产命名

`flow_name` 使用 `{模块}_{场景}`，例如 `基础配置_新增字典项`。证据 JSON 保存为
`evidence_assets/{模块}/{模块}_{场景}_asset.json`；截图使用
`{YYYYMMDD}_{用例ID}_{步骤号}_{成功/失败/待定/超时/留证}.png`；报告使用
`output_testcases/{模块}_{YYYYMMDD}_测试报告.md`。模块名和用例文档子表逐字一致，禁止使用
`final`、`new`、`副本`等无信息量后缀。

## 单条用例 SOP

每条按 **分析 → 执行 → 验证 → 留证**：

1. **分析**：有基线时先读取已验证 locator；无基线时分析页面结构和 iframe。动作描述必须写“动作 + 对象”，预期结果不得留空；
2. **执行**：首次执行先 `start_recording`；每步用 `execute_and_record` 或 `execute_action_chain` 执行 click/fill/select/date 等动作。定位优先级为稳定 CSS/Test ID/Label/Placeholder/ARIA，其次可见文本，最后才使用专用工具返回的精确坐标；
3. **验证**：用 `wait_for_condition` 等待成功提示、状态或目标元素；弹层用 `detect_overlays`；VTable 用 `vtable_*` 读取和断言；
4. **留证**：关键结果、失败或待定步骤必须截图；涉及下载时使用规范文件名保存下载素材；全部步骤完成后 `export_session` 导出证据和所需报表。

## 组件和 iframe 规则

- 嵌套 iframe 必须使用分析结果的完整 `frame_path`，不得假设 frame；
- Ant Design、Element Plus、SAP 的下拉/日期控件按实时 DOM 和 Portal 位置适配；
- Portal 弹层从 `<body>` 根节点查找，不能局限在原 iframe；
- VTable 是场景图/画布表格，绝不使用原生 table locator；
- 任何 VTable 表头图标、单元格或自绘浮层坐标，必须由 `vtable_analyze_headers`、
  `vtable_get_cell_center` 或 `detect_overlays` 返回，禁止截图估算；
- 页面切换 Tab、搜索、打开/关闭弹层后，重新确认页面上下文，并在 VTable 操作前刷新实例。

## 循环和失败处理

重复添加、翻页、重试等动作每次前后检查 `disabled`、`aria-disabled="true"`、
`ant-btn-disabled`/`is-disabled`/`disabled` 类名，以及列表行数或页面状态是否产生增量。
元素禁用、隐藏或无增量时立即停止。动作失败先读取错误，使用页面分析/弹层检测复核实况，
不要盲目重复同一动作；只有定位失效证据成立时才重新分析并更新 locator。

## 回归

默认模块回归；用户指定时支持全量、冒烟和模块回归。读取基线 locator 跳过重复分析；
回归只读基线，不覆盖基线文件。结果按用例编号合并并追加 `last_run`，定位器变化保留
`locator_history`。每条结果与基线比较，分类为通过→失败、失败→通过、新增失败、保持通过/失败；
状态翻转优先报告并留证。

## 结果回填与节奏

按项目用例文档映射逐条回填 `通过/不通过/待定`、执行人和当前执行时间。每完成一条用例，
立即报告编号、名称、结果和关键证据路径；失败/待定写明具体步骤、数据或字段。所有用例完成后，
按 `assets-and-reporting.md` 生成汇总、统计、回归对比和禅道交付信息。
