# VTable filter/list audit

## 目标

对 APS 列表页做三方一致性审查：筛选字段数量与业务列数量、同名字段文案、以及列单元格值集合 `C` 是否被对应值下拉待选值集合 `D` 完整覆盖（`C ⊆ D`）。

## 采集顺序

1. 用 `ui_page_context` 确认目标 Tab 的 `active_iframe`、frame id/name 和 focus layer；
2. 用 `ui_analyze_scope`（建议 `max_controls: 300`、`max_overlays: 5`）采集筛选控件；字段按「字段名 + 操作符 + 值控件」三元组计数，control/combobox 双节点只计一次；排除查询、设置、重置、收起、导出、分页和工具栏；
3. 用 `vtable_analysis`：`mode=full`、`visible_only=false`、`include_values=false`、`max_columns=30`，记录 title/field；排除 `_vtable_checkbox`、`_vtable_series_number` 和操作列后计业务列；
4. 对需要匹配的列，用 `vtable_read_cells` 按矩形读取数据并去重记录单元格值；表头 row0=0，数据从 row1；
5. 对每个值下拉，用 `ui_click` 点值控件（`selectUid*`/`.legions-pro-select`，不要点操作符），开启观察，从 `overlays[].text`/`changes[].text` 记录完整待选值。上一个下拉通常会自动关闭；遮挡或超时时先点页面空白处关闭浮层；
6. 以实测集合做差集和覆盖判断，不能放宽类型或文案：`3` 不等于 `3级`，`未审批` 不等于 `待审核`。

## 判定

- 筛选独有字段、列表独有列：配置不同步缺陷；
- 同一语义显示名不同：命名不一致；
- 列值不在下拉值中：字典编码未翻译或字典文案不一致；
- 布尔列空值只记观察项，不直接定性；
- 无对应列不做值匹配。

常见根因只能以“疑似”表述：列 format 未配置、两套字典并存、columns 与 queryItems 未同步维护。

## 证据与报告

报告应声明页面 URL、实际 iframe、测试时间、页码/行数范围、筛选字段全集、VTable 完整列清单、每个值下拉和列值集合、通过项及缺陷差集。按 `assets-and-reporting.md` 的禅道模板写入 `artifacts/`；一个缺陷一批并按严重度排序。

大结果若工具持久化为文本，只提取 JSON 中的 columns/values，不整读无关 base64。截图仅作证据，不作为列值或坐标数据源。
