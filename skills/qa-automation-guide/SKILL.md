---
name: qa-automation-guide
description: >-
  企业级 APS/SCM Web 质量自动化指南：测试设计、语义化 UI 执行、VTable 列表审计、
  数据权限双浏览器 E2E、证据资产复用与禅道缺陷报告。用户要求设计测试、执行 UI
  回归、审计筛选与列表一致性、或验证数据权限时调用。
disable-model-invocation: true
---

# QA Automation Guide

面向 APS/SCM（React + Ant Design + VTable）及相近 ToB 制造系统的统一 QA 技能。
本入口只保留路由、硬性约束和任务契约；具体流程按需读取 `references/`，避免一次性加载全部规则。

## 适用任务

- 设计 APS/SCM 功能测试矩阵和标准用例；
- 执行已有用例的 UI 首次录制、回归复验和证据留存；
- 审计列表筛选区、VTable 业务列及下拉值的一致性；
- 验证数据范围表、角色操作级权限和不同账号的数据隔离；
- 生成测试报告或禅道 BUG 模板。

## 不做什么

- 不凭截图或视觉估算坐标；
- 不猜测动态 MCP 工具名和参数，先发现并阅读 schema；
- 不把 VTable 当作原生 HTML table 操作；
- 不盲目循环点击、翻页或重试；
- 不编造未实测的数量、字段、下拉值或业务规则；
- 不在导出的 xlsx 中直接手改权威用例，权威源以 `testcase_json/` 为准。

## 加载顺序

1. `references/00-routing.md`：按任务选择后续文件；
2. `references/test-design.md`：设计/生成用例时读取；
3. `references/ui-execution.md`：UI 录制、执行、回归时读取；
4. `references/vtable-audit.md`：筛选区与 VTable 一致性审计时读取；
5. `references/data-permission.md`：数据权限、范围表、双账号隔离时读取；
6. `references/assets-and-reporting.md`：证据、报告、回填、禅道交付时读取；
7. `references/aps_module_map.md`：模块编号或 APS 业务约束需要时读取。

## 全局执行契约

### 1. 工具发现

使用 qa-automation MCP 前，先用 `tool_search` 搜索当前可用工具，至少覆盖：

- 页面/动作：`page click input select screenshot`
- VTable：`vtable`
- 弹层：`modal popup dialog 弹层 弹窗`
- 会话：`session browser chrome`

严格以检索结果的实时 schema 调用，不凭记忆补参数。

### 2. 定位与页面状态

优先级固定为：稳定 CSS/Test ID/Label/Placeholder/ARIA → 可见文本 → 专用工具返回的精确坐标。
先确认当前页面、激活 iframe 和浮层，再执行动作。Ant Design Portal 弹层从 `<body>` 根节点查找；切换 Tab 或页面状态改变后重新确认上下文。

### 3. 循环与批量动作

每次重复动作前后检查 `disabled`、`aria-disabled="true"`、`ant-btn-disabled`/`is-disabled`/`disabled` 类名，以及列表/行数/页面状态是否产生增量。元素禁用、隐藏或无增量时立即停止，不得继续点击或盲目重试。

### 4. 结果判定

- **通过**：实测页面数据和流程完全符合步骤、数据与预期；
- **不通过/失败**：至少一项实测结果与预期不符；
- **待定**：用例材料、页面字段或环境问题使结论无法可靠判定。

证据必须标明实测范围、时间、页面上下文和数据来源。任务完成后按对应 reference 的交付格式输出。

## APS 默认画像

APS 为多基地制造排程平台；ERP 同步客户、物料、BOM 等主数据，APS 侧通常只读；常见页面为多 Tab、Portal 弹层和 VTable 场景图。食品制造需关注 CCP、批次/保质期、清洗改机、得率、配方保密、多基地隔离和委外协同。若用户提供的系统规则不同，以用户材料和实测页面为准。
