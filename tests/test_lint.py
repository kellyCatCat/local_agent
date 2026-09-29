from app.lint import lint_draft
from tests.conftest import fixture, multi_files


def msgs(issues, level=None):
    return [i["message"] for i in issues if level is None or i["level"] == level]


def test_single_fixture_clean():
    assert lint_draft({"SKILL.md": fixture("single_skill.md")}) == []


def test_multi_fixture_clean():
    assert lint_draft(multi_files()) == []


def test_missing_main():
    assert "缺少主文件 SKILL.md" in msgs(lint_draft({"reference/a.md": "x"}))


def test_bad_name_and_section_order():
    text = fixture("single_skill.md").replace("name: srv6-te-policy-down", "name: SRv6_Policy")
    text = text.replace("# 前置检查", "# 临时").replace("# 入参列表", "# 前置检查").replace("# 临时", "# 入参列表")
    errs = msgs(lint_draft({"SKILL.md": text}), "error")
    assert any("英文 slug" in m for m in errs)
    assert any("顺序" in m for m in errs)


def test_step_numbering_and_bad_jump():
    text = fixture("single_skill.md").replace("## 步骤2：", "## 步骤3：").replace("顺序执行步骤2", "跳转步骤9")
    errs = msgs(lint_draft({"SKILL.md": text}), "error")
    assert any("连续" in m for m in errs)
    assert any("不存在的步骤9" in m for m in errs)


def test_root_cause_must_match_table():
    text = fixture("single_skill.md").replace("   - bfd 检测 Down", "   - BFD 会话 Down")
    errs = msgs(lint_draft({"SKILL.md": text}), "error")
    assert any("BFD 会话 Down" in m and "根因对照表" in m for m in errs)


def test_precheck_cannot_use_optional_param():
    text = fixture("single_skill.md").replace(
        "`display srv6-te policy endpoint <endpoint-ipv6> color <color-id>`\n   - 采集内容",
        "`display bfd session srv6-segment-list <segment-list-id>`\n   - 采集内容", 1)
    assert any("非必填参数 <segment-list-id>" in m for m in msgs(lint_draft({"SKILL.md": text}), "error"))


def test_param_hash_and_placeholder():
    text = fixture("single_skill.md").replace("<segment-list-id>`（仅当", "<segment-list-id c8be5e6454> {vpn}`（仅当")
    out = msgs(lint_draft({"SKILL.md": text}))
    assert any("抽取哈希" in m for m in out)
    assert any("占位符" in m for m in out)


def test_multi_unlisted_ref_and_missing_file():
    files = multi_files()
    files["reference/orphan.md"] = files.pop("reference/neighbor-flap.md")
    errs = lint_draft(files)
    assert any(i["file"] == "reference/orphan.md" and "没有在 SKILL.md" in i["message"] for i in errs)
    assert any("reference/neighbor-flap.md」不存在" in m for m in msgs(errs))


def test_multi_scenario_title_mismatch_and_jump_target():
    files = multi_files()
    files["reference/neighbor-down.md"] = files["reference/neighbor-down.md"].replace("# 场景A：IS-IS 邻居无法建立", "# 场景A：邻居建立失败")
    files["SKILL.md"] = files["SKILL.md"].replace("→ **场景B：IS-IS 邻居震荡**", "→ **场景C：不存在**")
    errs = msgs(lint_draft(files), "error")
    assert any("不一致" in m for m in errs)
    assert any("场景C：不存在" in m for m in errs)
    assert any("场景B：IS-IS 邻居震荡」没有出现在场景跳转表" in m for m in errs)


def test_multi_ref_name_semantics_and_frontmatter():
    files = multi_files()
    body = files.pop("reference/neighbor-down.md").replace("name: neighbor-down", "name: scenario-a")
    files["reference/scenario-a.md"] = body
    files["SKILL.md"] = files["SKILL.md"].replace("reference/neighbor-down.md", "reference/scenario-a.md")
    assert any("应有语义" in m for m in msgs(lint_draft(files), "error"))


def test_multi_steps_not_in_main():
    files = multi_files()
    files["SKILL.md"] += "\n## 步骤1：不该在这里\n"
    assert any("只放参考文件表" in m for m in msgs(lint_draft(files), "error"))


def test_backtick_redirect_flagged():
    files = multi_files()
    files["reference/neighbor-down.md"] = files["reference/neighbor-down.md"].replace(
        "转交：「收集信息并转技术支持」", "`转交：「收集信息并转技术支持」`")
    assert any("反引号" in m for m in msgs(lint_draft(files)))
