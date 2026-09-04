# Reference routing

根据用户任务只加载命中的 reference；不需要的文件不要读取。

| 用户意图 | 必读 | 可选 |
|---|---|---|
| 设计/补充 APS 功能用例 | `test-design.md` | `aps_module_map.md`、`assets-and-reporting.md` |
| 首次 UI 录制或已有用例执行 | `ui-execution.md` | `test-design.md`、`assets-and-reporting.md` |
| 回归复验/复用历史定位器 | `ui-execution.md`、`assets-and-reporting.md` | `test-design.md` |
| 筛选区与 VTable 列审计 | `vtable-audit.md` | `assets-and-reporting.md` |
| 数据范围表/角色范围/越权隔离 | `data-permission.md` | `ui-execution.md`、`assets-and-reporting.md` |
| 禅道报告或证据交付 | `assets-and-reporting.md` | `vtable-audit.md` |

## 任务开始前必须收集

- 被测系统 URL、目标菜单/页面和实际账号；
- 测试用例来源（文档路径/附件/子表/过滤条件）；
- 执行人、证据根目录、报告路径和回归模式；
- 如为权限测试：管理端/测试端账号与端口、目标角色、范围表条件；
- 如为审计：目标列表页、页面数据范围和是否需要多页抽查。

缺少影响执行安全或结论的参数时，先列出缺项；不以猜测替代。
