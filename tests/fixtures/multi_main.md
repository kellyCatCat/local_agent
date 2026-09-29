---
name: isis-troubleshooting
description: IS-IS 邻居异常。出现 IS-IS 邻居 Down/震荡告警时使用。
---

# 入参列表

| 信息 | 是否必填 | 说明 |
| --- | --- | --- |
| 网元ID | 是 | 网元的resId |
| 接口名 | 是 | 告警接口 |

# 前置检查

1. **查询邻居**
   - CLI 命令：`display isis peer verbose`
   - 采集内容：邻居状态
   - 适用场景：全部场景

2. **查询告警**
   - CLI 命令：`display alarm active verbose`
   - 采集内容：告警列表
   - 适用场景：场景B：IS-IS 邻居震荡（读数用于分流判断，其他场景可跳过）

## 场景跳转表

| 前置检查步骤 | 判据 | 跳转场景 |
| --- | --- | --- |
| 步骤 1（`display isis peer verbose`） | 邻居状态为 `Down` | → **场景A：IS-IS 邻居无法建立** |
| 步骤 2（`display alarm active verbose`） | 存在 `isisAdjacencyChange` 告警 | → **场景B：IS-IS 邻居震荡** |

# 排查步骤

进入对应场景后，先读取 `reference/` 目录下该场景的参考文件：

| 场景 | 参考文件 | 内容 |
| --- | --- | --- |
| 场景A：IS-IS 邻居无法建立 | reference/neighbor-down.md | 排查步骤 + 根因对照表 |
| 场景B：IS-IS 邻居震荡 | reference/neighbor-flap.md | 排查步骤 + 根因对照表 |
