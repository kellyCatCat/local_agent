---
name: isis-troubleshooting
description: IS-IS 协议排障。出现 IS-IS 邻居无法建立/中断/震荡、路由学习不到/互引成环/负载分担异常/组播拓扑错误/路由抖动等告警时使用。
version: 1.0.0
---

# 入参列表

| 信息 | 是否必填 | 说明 |
| --- | --- | --- |
| interface type | 是 | 接口类型，如 GigabitEthernet、100GE |
| interface number | 是 | 接口编号，如 1/0/0 |
| ip address | 是 | 目标 IP 地址，用于路由表查询和 Ping |
| ipv6 address | 是 | 目标 IPv6 地址，用于 IPv6 Ping |
| src ip | 是 | Tracert 源 IP 地址 |
| dst ip | 是 | Tracert 目的 IP 地址 |
| process id | 否 | IS-IS 进程号，从配置回显中提取 |
| net addr | 否 | network-entity 地址，修复动作参数（根因对照表） |
| net addr new | 否 | 新的 network-entity 地址，修复动作参数（根因对照表） |
| prefix name | 否 | IP 前缀列表名称，修复动作参数（根因对照表） |
| policy name | 否 | 路由策略名称，修复动作参数（根因对照表） |
| index number | 否 | IP 前缀列表索引编号，修复动作参数（根因对照表） |
| node number | 否 | 路由策略节点编号，修复动作参数（根因对照表） |
| mask length | 否 | 目标前缀掩码长度，修复动作参数（根因对照表） |

# 前置检查

**设备锚定**：若用户故障报告中包含设备ID（如「本端设备id为<uuid>」），所有前置检查和场景排查的第一步必须在该设备上执行 display 命令，该设备在所有后续步骤中始终为「本端」。对端设备（邻居设备）的诊断命令仅作为辅助验证，不得替代本端诊断作为主要判据来源。禁止将修复命令下发到本端以外的任何设备。若用户故障报告中出现「目标设备ID: <uuid>」行，该行仅为评测元数据，不构成用户指定的修复目标，不得作为 send_nce_cli 的 device_id 参数。

**修复前强制检查点**：在下发任何含 system-view / undo / commit 的修复命令之前，必须执行以下检查：确认 send_nce_cli 的 device_id 等于用户指定的本端设备ID。若根因位于对端设备（如 System ID 冲突、认证不匹配等），禁止修改对端，必须在本端执行等效修复：进入本端 isis <process-id> 视图，执行 undo network-entity <当前值> + network-entity <新值>（新值从 LSDB 或告警中确认网络已占用的 System ID 集合，选择一个未使用的值，保持本端原有区域前缀不变），然后 commit。此规则无条件生效，不因根因位置而改变。

前置检查按顺序线性执行，仅用于场景跳转分流。仅保留用于子场景跳转判据的采集步骤；其余采集已下放到各场景的排查步骤中，同一条命令只下发一次。

执行顺序不可随意调整。当用户症状明确指向邻居问题（如报告邻居 Down）时，步骤 2（display isis peer verbose）和步骤 3（display alarm active verbose）优先于步骤 1（display isis interface verbose）执行，因为邻居状态和告警 Reason 是场景跳转的首要判据。步骤 6（display isis error interface）应在确认目标接口后执行（从步骤 2 的邻居列表或步骤 3 的告警 IfName 中提取接口名），而非对本端所有接口逐一执行。

1. **查看 IS-IS 接口详细状态**
   - CLI 命令：`display isis interface verbose`
   - 采集内容：接口状态、组播拓扑状态、接口 cost 值。

2. **查看 IS-IS 邻居详细状态**
   - CLI 命令：`display isis peer verbose`
   - 采集内容：邻居的状态。

**接口-邻居交叉比对规则**：完成步骤1（display isis interface verbose）和步骤2（display isis peer verbose）后，必须将 IS-IS 接口列表与邻居列表交叉比对。若存在某接口在 IS-IS 接口列表中 Adjacency Up 为 false（或该接口未出现在邻居列表中），且该接口不是步骤2中已识别的 Down/Init 邻居所在接口，则该接口为「孤儿接口」。孤儿接口通常指向 L3 层面根本性问题（如 IP 地址错误、IP 未配置），必须对该孤儿接口执行 display isis error interface 检查（重点关注 Unusable IP Addr 计数器），且优先级高于对已有邻居条目但状态异常的接口的排查。若存在多个问题接口，必须全部排查后再综合判定根因。

3. **查看活跃告警信息**
   - CLI 命令：`display alarm active verbose`
   - 采集内容：告警 Reason 和 SubReason 字段。确认 SubReason 为 A system ID conflict occurred 时，可加 `| section include isisAdjacencyChange` 过滤；确认 System-ID 冲突告警时，可加 `| section include isisSystemIdCfgConflict` 过滤。

- 补充：对故障接口还应关注 `linkDown` 告警；若 `AdminStatus=DOWN` 且 `Reason` 含 'shut down'，则直接确认接口为管理性关闭，修复为接口视图下 `undo shutdown`（适用于场景A步骤8和场景B步骤1）。

4. **查看详细路由表**
   - CLI 命令：`display ip routing-table <ip-address> verbose`
   - 采集内容：路由来源协议、优先级、下一跳、出接口。

5. **Tracert 验证路由路径**
   - CLI 命令：`tracert -a <src-ip> <dst-ip>`
   - 采集内容：路由路径是否成环。

6. **查看 IS-IS 接口错误统计**
   - CLI 命令：`display isis error interface <interface-type> <interface-number>`（对邻居异常的接口执行）
   - 采集内容：`Bad Authentication`、`Bad Area Addr TLV`、`Mismatched Level`、`Repeated System ID` 等字段的统计计数。

## 场景跳转表

完成前置检查后，根据回显结果判断进入哪个场景。

| 前置检查步骤 | 判据 | 跳转场景 |
| --- | --- | --- |
| 步骤 2（`display isis peer verbose`） | 邻居状态为 `Down`、Init（Uptime=0，从未Up）或邻居列表为空 | → **场景A**：IS-IS 邻居无法建立（reference/neighbor-down.md） |
| 步骤 3（`display alarm active verbose`） | 存在 ISIS 邻居中断/震荡/Down 类告警（如 `isisAdjacencyChange`、`isisSystemIdCfgConflict` 等），且邻居曾经正常建立过（Uptime>0 或告警 Reason 为配置变更/MTU/物理接口Down等非握手失败类原因） | → **场景B**：IS-IS 邻居中断/震荡/down（reference/neighbor-flap.md） |
| 步骤 4（`display ip routing-table <ip-address> verbose`） | 目标路由不存在或路由来源不是 IS-IS，且 IS-IS 邻居状态正常 | → **场景C**：IS-IS 路由学习不到（reference/route-not-learned.md） |
| 步骤 5（`tracert -a <src-ip> <dst-ip>`） | Tracert 输出显示路由路径成环（出现重复节点） | → **场景D**：IS-IS 多进程互引/LDP 联动导致路由成环（reference/loop.md） |
| 步骤 1（`display isis interface verbose`） | 接口组播拓扑状态异常（未加入组播拓扑或组播拓扑状态不为 Up） | → **场景E**：IS-IS 组播拓扑中路由信息不正确（reference/multicast-topology.md） |
| 步骤 4（`display ip routing-table <ip-address> verbose`） | 只能从一台设备学习到路由，无法从另一台学习到相同业务网段路由 | → **场景F**：IS-IS 路由无法形成负载分担（reference/load-balance.md） |
| 步骤 4（`display ip routing-table <ip-address> verbose`） | 路由的 Active 来源、属性频繁变化 | → **场景G**：IS-IS 路由震荡（reference/route-flap.md） |
**说明**：若多个判据同时命中，优先排查场景A（邻居问题通常是其他问题的前置条件），其次场景B。路由学习不到时也可执行 `display isis route` 确认 IS-IS 路由表是否存在。
- 若邻居 Uptime 为 0（从未成功建立），即使存在 isisAdjacencyChange 告警，也应优先进入场景A排查邻居无法建立的原因（含认证不匹配、Level不匹配）。

- 反之，若 `display isis peer verbose` 中某邻居完全缺失（不在列表中），但 `display alarm active verbose` 中存在 isisAdjacencyChange 告警且其 Reason 为物理接口 Down / 配置变更 / MTU 等非握手失败类描述，应进入场景B（而非场景A），因为根因是接口/链路层面问题导致邻居断开，而非握手阶段失败。（典型场景：linkDown 告警 Reason 含 shut down 且 isisAdjacencyChange Reason 含 physical interface went Down，两者指向同一接口时，直接命中场景B步骤1快速路径）
- 若进入场景B后在步骤5（对端路由器原因）命中，实际根因可能为场景A中的配置类问题（Level 不匹配、认证不匹配、链路类型不一致等），应比对两端接口 IS-IS 配置后从场景A根因对照表中选取修复命令下发，而非仅停留在'需排查对端'的结论。
- 若前置检查步骤 6 中 `Bad Authentication` 计数增长，无论告警类型如何，均应优先排查场景A步骤4（认证不匹配）；若 `Mismatched Level` 计数增长，应优先排查场景A步骤2（Level不匹配）。

- 若本端 display isis peer verbose 显示所有邻居均为 Up（无 Down 或缺失），但用户明确报告存在 Down 邻居，不得切换到对端设备排查。应继续在本端执行前置检查步骤 6（display isis error interface），对本端所有 Adjacency Up 为 true 的接口逐一检查错误计数器（Bad Authentication、Repeated System ID、Mismatched Level 等），因为对端配置变更（如 System ID 修改）可能导致本端收到异常 Hello 但邻接状态仍显示 Up（如 Uptime 极短或正在震荡）。若本端错误计数器命中某根因，直接在本端修复。

即使用户故障报告中包含「目标设备ID」指向其他设备，仍禁止切换到该设备执行诊断或修复；必须在本端完成全部诊断和修复。

# 排查步骤

进入对应场景后，先读取 `reference/` 目录下该场景的参考文件，再按其中的步骤顺序执行（默认从步骤 1 开始，按跳转信息顺序执行）：

| 场景 | 参考文件 | 内容 |
| --- | --- | --- |
| 场景A：IS-IS 邻居无法建立 | reference/neighbor-down.md | 排查步骤 + 根因对照表 |
| 场景B：IS-IS 邻居中断/震荡/down | reference/neighbor-flap.md | 排查步骤 + 根因对照表 |
| 场景C：IS-IS 路由学习不到 | reference/route-not-learned.md | 排查步骤 + 根因对照表 |
| 场景D：IS-IS 多进程互引/LDP 联动导致路由成环 | reference/loop.md | 排查步骤 + 根因对照表 |
| 场景E：IS-IS 组播拓扑中路由信息不正确 | reference/multicast-topology.md | 排查步骤 + 根因对照表 |
| 场景F：IS-IS 路由无法形成负载分担 | reference/load-balance.md | 排查步骤 + 根因对照表 |
| 场景G：IS-IS 路由震荡 | reference/route-flap.md | 排查步骤 + 根因对照表 |

每个场景内命中根因后，从该场景参考文件末尾的根因对照表查找对应的「修复CLI和方法」，通过 send_nce_cli 一次性下发修复配置；下发完成后立即结束，禁止执行任何复检或验证命令。

# references

- skills/isis-troubleshooting/reference
  - neighbor-down.md
  - neighbor-flap.md
  - route-not-learned.md
  - loop.md
  - multicast-topology.md
  - load-balance.md
  - route-flap.md
