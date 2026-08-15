---
name: qa-automation-guide
description: 生和堂 APS 企业级 Web 自动化测试设计指南、食品行业用例矩阵设计、高韧性定位与 SOP 执行标准
---

# 生和堂 APS 自动化测试与用例设计 SOP 指南

本技能为**生和堂 APS（高级计划排程）系统**定制。APS 是食品制造企业（龟苓膏、养生饮品、果汁系列）的多基地协同排产平台，对接金蝶 ERP、MES、WMS 等外部系统。本指南指导 AI Agent 开展全自动用例录制、动态 UI 组件适配、高韧性语义定位以及 Shadcn 极简风 Excel 报表导出。

> 被测系统画像（供每次会话前快速对齐）：
> - 系统名称：生和堂 APS 智能排程；入口为工作台首页（左侧可折叠导航 + 标签页内容区）。
> - 数据来源：客户/物料/BOM 等主数据从金蝶 ERP 同步，APS 侧只读，同步数据不允许在 APS 修改。
> - 食品行业约束：CCP 关键控制点、保质期批次、清洗改机时间损失、得率波动、配方保密（字段级权限）。
> - 页面形态：Ant Design 门户弹层（Portal 挂载于 `<body>` 根节点）、多标签页 Tab 面板、表格为 VTable 场景图渲染。
> - 多基地：江门总部、徐州基地、凭祥基地，预测按基地/产线拆分，支持委外/代工协同。

---

## 1. 核心测试设计模式矩阵 (Test Matrix)

在设计测试场景或生成步骤序列时，**禁止仅设计简单的 Happy Path 脚本**。必须自动将目标页面功能解构为以下 5 种测试模式：

### Pattern A: Happy Path (正向黄金业务流模式)
- **目标**：验证默认/中位数数据下的完整端到端业务闭环。
- **规则**：映射上游单据依赖（如预先加载 PO 单自动带出物料），断言终态（如 `单据状态: 已审核`）。
- **APS 落地**：销售预测 → 主生产计划 → MRP 运算 → 制造排程 → 出货的全链路闭环；同步数据（ERP 主数据）只读校验。

### Pattern B: Boundary Value Analysis (边界值极限校验模式)
- **目标**：挖掘数值、容积、日期与字符长度的数据库/业务规则极限。
- **规则**：识别字段物理上限（如库位最大承重 `5000kg`），自动设计三组输入：
  - **内边界**：`4999`（预期成功）
  - **上边界**：`5000`（预期成功）
  - **外边界**：`5001`（预期出现友好预警并阻断提交）
- **APS 落地**：排产数量/预测数量与产线产能、UPH、工作日历的边界；报工数量不得超派工计划数（超报拦截）；保质期/日期字段的临界日校验。

### Pattern C: Negative & Validation Blocking (负向及异常流拦截模式)
- **目标**：确保系统能安全拦截非法数据与逻辑违规。
- **规则**：设计至少 2 个负向步骤：
  - 必填项空值提交拦截（前端/后端校验提示）。
  - 超报拦截（如报工数量 101 大于派工单计划数 100）。
  - 异常字符/防注入测试。
- **APS 落地**：清洗改机规则的原产品类型与目标产品类型相同必须拦截；排产计划时间与工作日历冲突拦截；ERP 同步数据的只读字段修改拦截。

### Pattern D: State Transition Machine (单据生命周期控制模式)
- **目标**：验证用户权限与 UI 控件状态是否随单据生命周期正确变化。
- **规则**：断言按钮可见性与可编辑性（如单据处于"已关单"或"已审核"状态时，"编辑"与"删除"按钮必须置灰或隐藏）。
- **APS 落地**：制造计划（草稿→确定→锁定→关单）、审批流（提交→待审→已审/驳回）、插单申请的状态机；锁定排程后不允许再编辑。

### Pattern E: Exception & Hardware Tolerance (容错与硬件交互模式)
- **目标**：验证扫码枪重复扫码、连击防抖及网络延迟下的系统容错能力。
- **APS 落地**：仓储/车间场景的扫码录入防抖、接口同步失败的重试与告警（接口与日志模块支持失败重试与异常通知）。

---

## 2. 框架穿透与 UI 适配路由规则

企业级 Web 系统交互复杂，必须动态选择适配器与 iframe 路径，避免易碎定位。生和堂 APS 为 React + Ant Design + VTable 技术栈：

### A. 嵌套 iframe 自动穿透
- 始终分析 `analyze_current_page` 返回的 `frame_path` 链。
- 目标元素在 iframe 内时，使用链式选择器传给 `iframe_selector`（如 `#main-iframe -> iframe[name="sub-grid"]`）。
- APS 表格为 VTable 渲染，优先走 `vtable_*` 工具族（如 `vtable_get_column_values` / `vtable_select_rows`），避免脆弱的 canvas 坐标定位。

### B. UI 组件适配器行为路由
原生点击往往无法激活门户节点（Portal），需路由到对应适配器：
- **Ant Design (React) — APS 主技术栈:**
  - *下拉选择 (`select_option`):* 优先通过 `role=option` 语义定位，Portal 下拉层位于 `<body>` 根节点，自动穿透 iframe 上下文。
  - *日期选择 (`fill_date`):* 自动定位内部 input，解绑 `readonly` 并提交回车。
  - *表格:* VTable 场景图，用 `vtable_*` 工具取数/勾选，不用原生 locator 对 canvas 定位。
- **Element Plus (Vue):**
  - *下拉选择 (`select_option`):* 触发框点击后，跳至 `<body>` 根节点检索 `.el-select-dropdown__item` 匹配文本。
  - *日期选择 (`fill_date`):* 移除 `readonly` 后原生填充并回车。
- **SAP Fiori:**
  - *下拉选择 (`select_option`):* 点击组合框，等待 `.sapMComboBoxBoxItem` 或 `.sapMSelectListItem` 渲染后匹配文本点击。

### C. 门户弹层要点 (APS 高频交互)
- 弹窗/下拉/通知均经 Portal 挂载到 `<body>` 根节点：点击触发后，从根节点检索 `role=dialog` / `role=option`，不要受 iframe 上下文限制。
- 多标签页结构：切换 Tab 后 DOM 可能整体重建，等待元素需以可见性为准（`wait_for visible`），并用 `observe_after_click` 观察动态层变化。

---

## 3. 用例资产与 16 字段规范对接

自动化录制的用例最终以 16 字段 JSON 结构汇入公司双资产库（`testcase_json/` 权威源 + `testcase_xlsx/` 导出表）。录制产出需按以下约定对齐：

| 资产 | 工具 | 落盘位置 | 说明 |
|---|---|---|---|
| 会话证据 JSON | `export_session` | `evidence_assets/` | 步骤级证据（动作、定位器、iframe 路径） |
| Shadcn Excel 用例 | `export_session` | `output_testcases/` | 操作步骤/动作/定位器/测试数据/预期结果 |
| 权威用例 JSON | `tob-testcase-generator` 技能 | `testcase_json/` | 16 字段对象数组：用例编号/级别/模块/验证点/前置条件/测试步骤/测试数据/预期结果… |

字段级对齐规则（录制 Excel → 16 字段 JSON 的映射约定）：
- `用例编号`：`{SYS}_{MOD}_{4位序号}`，如 `APS_CPGY_QXGJ_0001`；完整模块缩写表见 `references/aps_module_map.md`。
- `功能` 统一切片：`列表 / 搜索 / 新增 / 编辑 / 删除 / 导出 / 启用 / 停用 / 数据权限 / 审批`。
- `级别`：高级（冒烟/主流程/数据权限与越权）、中级（常规分支/组合过滤/状态流转）、低级（异常输入/边界/UI 细节）。
- `编写人` / `编写时间`：沿用公司规范（`YYYY/M/D`）。

---

## 4. 标准执行 SOP (Standard Operating Procedure)

当接收到 APS 自动化测试需求时，严格按以下步骤执行：

1. **环境与 DOM 探查**：调起 `analyze_current_page` 截取 DOM、iframe 树及 UI 框架指纹；确认目标标签页与门户层结构。
2. **用例矩阵规划**：按第 1 节 5 种模式打印 Markdown 格式的用例大纲（覆盖正向、边界与负向拦截场景），供用户确认。
3. **开启会话**：用户确认后调起 `start_recording` 初始化内存会话缓冲，按 16 字段规范预填系统标识（`system_under_test: 生和堂APS`）与流程名。
4. **高韧性交互录制**：按顺序调用 `execute_and_record` 或 `execute_action_chain` 执行动作，优先选用 Test ID、Label、Placeholder 或 ARIA Role 等面向用户的语义化定位器；表格断言走 `vtable_*` 工具；每次点击后依赖 `observe_after_click` 捕获动态层与消息反馈。
5. **极美资产落盘**：调用 `export_session` 将 JSON 证据保存至 `evidence_assets/`，并将 Shadcn Slate 双色调极简 Excel 报表落盘至 `output_testcases/`；如需进入权威用例库，进一步用 `tob-testcase-generator` 技能按 16 字段规范生成 `testcase_json/` 资产并联动刷新 `testcase_xlsx/`。

### 4.1 环境约定速查
- 服务默认在 APS 项目根目录启动，使资产自然落入 `evidence_assets/` 与 `output_testcases/`。
