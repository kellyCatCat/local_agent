import io

from openpyxl import Workbook

from app.parser import parse_cli_source
from app.provenance import check_cli_sources, extract_commands, norm_cmd
from tests.conftest import fixture, multi_files


def step_table(rows, headers=("子场景", "步骤详细描述", "命令行", "回显", "配置修复建议", "修复验证，怎么验证")):
    wb = Workbook()
    ws = wb.active
    ws.title = "说明"
    ws.append(["这是说明页，没有步骤表表头"])
    ws2 = wb.create_sheet("步骤表")
    ws2.append(["OSPF 步骤表"])          # 表头不一定在第一行
    ws2.append(list(headers))
    for r in rows:
        ws2.append(list(r))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def uploads(*rows):
    return [{"name": "steps.xlsx", "cli": parse_cli_source("steps.xlsx", step_table(rows))}]


SKILL = """# 前置检查

1. **查询邻居**
   - CLI 命令：`display isis peer verbose`（查看 `State` 字段）
   - 采集内容：`State`、后续 `<process-id>` 取自此处

# 排查步骤

## 步骤1：检查 BFD

1. **步骤名称**：检查 BFD
2. **CLI 命令**：
   - `display bfd session srv6-segment-list <segment-list-id>`（仅当 `BFD State` 为 `Down` 时执行）
   - `display isis <process-id> error`
3. **跳转信息**：
   - `BFD State` 为 `Down`：定位根因。
4. **根因定位**：
   - bfd Down

# 根因对照表

| 根因 | 现象 | 修复CLI和方法 | 复检命令（可选） |
| --- | --- | --- | --- |
| bfd Down | `BFD State` 为 `Down` | `isis <process-id>`<br>`undo shutdown` | `display isis peer verbose` |
"""


def test_parse_cli_source_detects_columns():
    src = parse_cli_source("a.xlsx", step_table([("A", "看邻居", "display isis peer verbose", "ospf 1", "undo shutdown", "")]))
    assert src["sheets"] == ["步骤表"]
    assert "display isis peer verbose" in src["cmd"] and "undo shutdown" in src["cmd"]
    assert src["echo"] == "ospf 1"
    assert parse_cli_source("a.md", b"x") is None
    assert parse_cli_source("a.xlsx", step_table([], headers=("甲", "乙"))) is None


def test_extract_only_command_positions():
    cmds = set(extract_commands({"SKILL.md": SKILL}))
    assert cmds == {
        "display isis peer verbose",
        "display bfd session srv6-segment-list <segment-list-id>",
        "display isis <process-id> error",
        "isis <process-id>",
        "undo shutdown",
    }  # 字段 `State`、`BFD State`，括号里的说明、采集内容里的参数都不算


def test_norm_cmd_param_and_space_variants():
    assert norm_cmd("display  interface {interface_type}  <Interface Number>") == norm_cmd("display interface <interface-type> <interface-number>")
    assert norm_cmd("display bgp peer <peer ip c8be5e6454>") == norm_cmd("display bgp peer <peer-ip>")


def test_check_reports_missing_and_echo_only():
    up = uploads(
        ("A", "看邻居", "display isis peer verbose", "isis 1\n undo shutdown", "", ""),
        ("A", "", "display bfd session srv6-segment-list {segment_list_id}", "", "isis <process-id>", ""),
    )
    issues, note = check_cli_sources({"SKILL.md": SKILL}, up)
    msgs = {i["message"] for i in issues}
    assert msgs == {
        "CLI `display isis <process-id> error` 在源步骤表的命令来源列中找不到出处",
        "CLI `undo shutdown` 只出现在「回显」列，回显不是命令来源",
    }
    assert all(i["level"] == "error" for i in issues) and "steps.xlsx" in note


def test_modify_only_checks_new_commands():
    up = uploads(("A", "", "display isis peer verbose", "", "", ""))
    base = {"SKILL.md": SKILL}
    assert check_cli_sources(dict(base), up, base)[0] == []
    draft = {"SKILL.md": SKILL.replace("`undo shutdown`", "`undo shutdown`<br>`isis cost 10`")}
    issues, _ = check_cli_sources(draft, up, base)
    assert [i["message"] for i in issues] == ["CLI `isis cost 10` 在源步骤表的命令来源列中找不到出处"]


def test_non_excel_sources():
    issues, note = check_cli_sources({"SKILL.md": SKILL}, [{"name": "a.md", "cli": None}])
    assert issues == [] and "只支持 Excel" in note
    # Excel 与非 Excel 混合：命令可能来自非 Excel 文档，降为提醒
    up = uploads(("A", "", "display isis peer verbose", "", "", "")) + [{"name": "b.md", "cli": None}]
    issues, note = check_cli_sources({"SKILL.md": SKILL}, up)
    assert issues and all(i["level"] == "warning" for i in issues) and "b.md" in note


def test_user_standard_skill_commands():
    files = {"SKILL.md": fixture("isis/SKILL.md"), "reference/load-balance.md": fixture("isis/reference/load-balance.md")}
    assert set(extract_commands(files)) == {
        "display isis interface verbose", "display isis peer verbose", "display alarm active verbose",
        "display ip routing-table <ip-address> verbose", "tracert -a <src-ip> <dst-ip>",
        "display isis error interface <interface-type> <interface-number>",
        "isis <process-id>", "import-route direct cost-type internal", "import-route static cost-type internal",
    }
    assert "reference/neighbor-down.md" in multi_files()
