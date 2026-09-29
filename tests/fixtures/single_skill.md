---
name: srv6-te-policy-down
description: SRv6 TE Policy Down。出现 SRv6 TE Policy Down 告警时使用。
---

# 入参列表

| 信息 | 是否必填 | 说明 |
| --- | --- | --- |
| endpoint IPv6 | 是 | 告警中 SRv6 TE Policy 的 endpoint 地址 |
| color | 是 | 告警中的 color |
| segment-list ID | 否 | 从前置检查步骤 1 的回显中提取 |

# 前置检查

1. **查询目标 SRv6 TE Policy 状态**
   - CLI 命令：`display srv6-te policy endpoint <endpoint-ipv6> color <color-id>`
   - 采集内容：`Policy State`、`BFD State`、Segment List ID。
   - 适用步骤：全部步骤
   - 根因定位：若回显为空或提示该 Policy 不存在，判定根因为“SRv6 TE Policy 不存在”，结束排查。

## 步骤跳转表

| 前置检查步骤 | 判据 | 跳转场景 |
| --- | --- | --- |
| 步骤 1（`display srv6-te policy endpoint <endpoint-ipv6> color <color-id>`） | `BFD State` 为 `Down` | → **步骤 2：检查是否因 BFD Down 导致中断** |

# 排查步骤

## 步骤1：检查 SRv6 TE Policy 是否被 shutdown

1. **步骤名称**：检查 SRv6 TE Policy 是否被 shutdown
2. **CLI 命令**：复用前置检查步骤 1 回显（查看 `Policy State` 字段）
3. **跳转信息**：
   - `Policy State` 不为 `Down (Shutdown)`：顺序执行步骤2。
   - `Policy State` 为 `Down (Shutdown)`：定位根因，结束排查。
4. **根因定位**：
   - SRv6 TE Policy 被 shutdown

## 步骤2：检查是否因 BFD Down 导致中断

1. **步骤名称**：检查是否因 BFD Down 导致中断
2. **CLI 命令**：
   - `display bfd session srv6-segment-list <segment-list-id>`（仅当 `BFD State` 为 `Down` 时执行）
3. **跳转信息**：
   - `BFD State` 为 `Down`：定位根因，结束排查。
   - 全部判据都不命中：判定「未找到根因」，输出已执行的全部检查步骤及结果摘要，结束排查。
4. **根因定位**：
   - bfd 检测 Down

# 根因对照表

| 根因 | 现象 | 修复CLI和方法 | 复检命令（可选） |
| --- | --- | --- | --- |
| SRv6 TE Policy 不存在 | 回显为空 | 无直接修复CLI，只能定位 | - |
| SRv6 TE Policy 被 shutdown | `Policy State` 为 `Down (Shutdown)` | 在该 Policy 视图下执行 `undo shutdown` | `display srv6-te policy endpoint <endpoint-ipv6> color <color-id>` |
| bfd 检测 Down | `BFD State` 为 `Down` | 无直接修复CLI，只能定位 | - |
| 未找到根因 | 全部判据不命中 | 输出已执行的全部检查步骤及结果摘要 | - |
