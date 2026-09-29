from app.versioning import apply_bumps, get_version, plan_bumps, set_version

MAIN = "---\nname: x\ndescription: d\nversion: 1.0.0\n---\n\n# 入参列表\n"
REF = "---\nname: a\ndescription: d\nversion: 1.2.3\n---\n\n# 场景A：a\n"


def bumps(files, base):
    return {b["path"]: b["new"] for b in plan_bumps(files, base)}


def test_get_set_version():
    assert get_version(MAIN) == "1.0.0"
    assert get_version(set_version(MAIN, "1.0.1")) == "1.0.1"
    no_ver = "---\nname: x\n---\nbody\n"
    assert get_version(set_version(no_ver, "1.0.0")) == "1.0.0"
    assert set_version("no frontmatter", "1.0.0") == "no frontmatter"


def test_no_change_no_bump():
    base = {"SKILL.md": MAIN, "reference/a.md": REF}
    assert plan_bumps(dict(base), base) == []


def test_changed_ref_bumps_ref_and_main():
    base = {"SKILL.md": MAIN, "reference/a.md": REF}
    files = {"SKILL.md": MAIN, "reference/a.md": REF + "新增一步\n"}
    assert bumps(files, base) == {"SKILL.md": "1.0.1", "reference/a.md": "1.2.4"}


def test_added_or_deleted_file_bumps_main_only():
    base = {"SKILL.md": MAIN, "reference/a.md": REF}
    added = {**base, "reference/b.md": REF.replace("name: a", "name: b")}
    assert bumps(added, base) == {"SKILL.md": "1.0.1"}
    assert bumps({"SKILL.md": MAIN}, base) == {"SKILL.md": "1.0.1"}


def test_manual_higher_version_kept_and_version_only_edit_ignored():
    base = {"SKILL.md": MAIN}
    assert bumps({"SKILL.md": set_version(MAIN, "2.0.0") + "x\n"}, base) == {}
    # 只改了 version（例如改低了）不算内容改动
    assert bumps({"SKILL.md": set_version(MAIN, "0.9.0")}, base) == {}


def test_bump_is_based_on_library_version_not_draft():
    base = {"SKILL.md": MAIN}
    files = {"SKILL.md": set_version(MAIN, "0.1.0") + "x\n"}
    out = apply_bumps(files, plan_bumps(files, base))
    assert get_version(out["SKILL.md"]) == "1.0.1"


def test_new_skill_no_bump():
    assert plan_bumps({"SKILL.md": MAIN}, {}) == []
