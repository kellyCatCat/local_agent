from app.lint import check_preserved, lint_changes, lint_draft
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
    files["SKILL.md"] = files["SKILL.md"].replace("# references", "## 步骤1：不该在这里\n\n# references")
    assert any("只放参考文件表" in m for m in msgs(lint_draft(files), "error"))


def test_backtick_redirect_flagged():
    files = multi_files()
    files["reference/neighbor-down.md"] = files["reference/neighbor-down.md"].replace(
        "转交：「收集信息并转技术支持」", "`转交：「收集信息并转技术支持」`")
    assert any("反引号" in m for m in msgs(lint_draft(files)))


def isis_standard():
    return {
        "SKILL.md": fixture("isis/SKILL.md"),
        "reference/load-balance.md": fixture("isis/reference/load-balance.md"),
    }


def test_user_standard_skill_only_missing_refs():
    """用户提供的标准 skill：除了没附上的 6 个参考文件，不应有任何问题。"""
    issues = lint_draft(isis_standard())
    assert issues and all("不存在" in i["message"] for i in issues), issues
    assert len(issues) == 6


def test_jump_target_path_must_match_ref_table():
    files = isis_standard()
    files["SKILL.md"] = files["SKILL.md"].replace("（reference/load-balance.md）", "（reference/loop.md）")
    assert any("指向 reference/loop.md" in m for m in msgs(lint_draft(files), "error"))


def test_references_section_must_list_all_refs():
    files = multi_files()
    files["SKILL.md"] = files["SKILL.md"].replace("  - neighbor-flap.md\n", "  - stale.md\n")
    out = msgs(lint_draft(files))
    assert any("缺少 neighbor-flap.md" in m for m in out)
    assert any("stale.md 不在参考文件表中" in m for m in out)
    files["SKILL.md"] = files["SKILL.md"].split("# references")[0]
    assert any("缺少「# references」" in m for m in msgs(lint_draft(files)))


def test_version_format():
    files = multi_files()
    files["SKILL.md"] = files["SKILL.md"].replace("version: 1.0.0", "version: v1")
    assert any("x.y.z" in m for m in msgs(lint_draft(files)))


def test_preserved_prose_rules():
    base = isis_standard()
    assert check_preserved(base, base) == []
    draft = dict(base)
    draft["SKILL.md"] = draft["SKILL.md"].replace("禁止将修复命令下发到本端以外的任何设备。", "")
    draft["SKILL.md"] = draft["SKILL.md"].replace("  - route-flap.md\n", "")  # references 清单不算说明段落
    out = check_preserved(draft, base)
    assert len(out) == 1 and "设备锚定" in out[0]["message"] and out[0]["level"] == "warning"
    # 步骤、表格等结构性内容的修改不触发
    draft = dict(base)
    draft["reference/load-balance.md"] = draft["reference/load-balance.md"].replace("本子图未给出判定观测", "新现象")
    assert check_preserved(draft, base) == []
    # 段落移动到其他文件（如拆分为多场景）不算删除
    draft = dict(base)
    rule = "即使用户故障报告中包含「目标设备ID」指向其他设备，仍禁止切换到该设备执行诊断或修复；必须在本端完成全部诊断和修复。"
    draft["SKILL.md"] = draft["SKILL.md"].replace(rule, "")
    draft["reference/load-balance.md"] += "\n" + rule + "\n"
    assert check_preserved(draft, base) == []


def test_user_standard_skill_full_set_clean():
    """补齐 7 个场景文件（按 load-balance.md 的格式占位）后，用户的标准 skill 应零问题。"""
    files = isis_standard()
    lb = files["reference/load-balance.md"]
    for slug, title in {
        "neighbor-down": "场景A：IS-IS 邻居无法建立",
        "neighbor-flap": "场景B：IS-IS 邻居中断/震荡/down",
        "route-not-learned": "场景C：IS-IS 路由学习不到",
        "loop": "场景D：IS-IS 多进程互引/LDP 联动导致路由成环",
        "multicast-topology": "场景E：IS-IS 组播拓扑中路由信息不正确",
        "route-flap": "场景G：IS-IS 路由震荡",
    }.items():
        files[f"reference/{slug}.md"] = lb.replace("name: load-balance", f"name: {slug}").replace(
            "场景F：IS-IS 路由无法形成负载分担", title)
    assert lint_draft(files) == []


def test_lint_changes_only_reports_new_issues():
    base = isis_standard()  # 原版本身有 6 个「参考文件不存在」
    new, existing = lint_changes(dict(base), base)
    assert new == [] and len(existing) == 6

    draft = dict(base)
    draft["reference/load-balance.md"] = draft["reference/load-balance.md"].replace(
        "   - 不同厂商默认 cost-type 不一致", "   - cost-type 不一致")
    new, existing = lint_changes(draft, base)
    assert [i["message"] for i in new if i["level"] == "error"] == [
        "步骤1 的根因「cost-type 不一致」未出现在根因对照表中（需逐字一致）"]
    assert len(existing) == 6


def test_lint_changes_cross_file_consequence_counts_as_new():
    """删掉参考文件后，SKILL.md 本身没改，但由此产生的问题也要报出来。"""
    base = multi_files()
    draft = dict(base)
    del draft["reference/neighbor-flap.md"]
    new, _ = lint_changes(draft, base)
    assert any(i["file"] == "SKILL.md" and "neighbor-flap.md」不存在" in i["message"] for i in new)


def test_lint_changes_without_base_reports_all():
    files = {"SKILL.md": fixture("isis/SKILL.md")}
    new, existing = lint_changes(files, {})
    assert existing == [] and new == lint_draft(files)
