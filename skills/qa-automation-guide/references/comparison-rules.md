# VTable audit comparison rules

## 字段与列计数

筛选字段由字段名、操作符和值控件组成；以操作符和值控件数量交叉复核。文本值控件常为 `input.ant-input-sm`，日期范围常为 `input.ant-calendar-range-picker-input`，无 a11y name 且含 `selectUid*`/`.legions-pro-select` 的为值下拉。排除查询、设置、重置、收起、导出、分页和工具栏。

VTable 业务列来自 `vtable_analysis` 的 `title`/`field`；排除 `_vtable_checkbox`、`_vtable_series_number` 和操作列。

## 值覆盖

对应列全部实测单元格值组成集合 C，下拉全部待选值组成集合 D，必须严格满足 `C ⊆ D`。数字编码和翻译文案不视为相等；不同状态文案不视为相等；空布尔值只记录观察项。

## 根因措辞

只能写“疑似”：列缺少字典 format、列与筛选使用不同字典、columns 与 queryItems 未同步维护。

## 操作陷阱

- 多 iframe 先取 active iframe，所有带 frame 调用统一使用它；
- VTable 大结果或截图 base64 被持久化时，只提取需要的 JSON 字段或路径；
- 带 frame 的 viewport 截图可能不受支持，按实时 schema 选择元素截图或顶层截图；
- 值下拉被遮挡或点击超时，先点击页面空白处关闭可见浮层再继续；
- 任何坐标必须由 VTable/overlay 工具返回，禁止估算。
