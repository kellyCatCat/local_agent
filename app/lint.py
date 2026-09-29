"""按 skill_template 和本地标准 skill 的实际写法，对整个 skill 目录做静态检查（单故障 / 多场景两种布局）。

模板与本地标准 skill 冲突时以本地标准为准（见 templates/skill_conventions.md），例如：
前置检查条目不要求「适用场景」、跳转目标可带参考文件路径、SKILL.md 末尾可有 `# references` 章节。

结果展示在界面上，也可一键交给模型修正。检查是启发式的：error 基本可以确定违反规范，
warning 需要人判断。
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath

from .skills import MAIN_FILE, parse_frontmatter

SINGLE_SECTIONS = ["入参列表", "前置检查", "排查步骤", "根因对照表"]
MULTI_MAIN_SECTIONS = ["入参列表", "前置检查", "排查步骤"]
REFS_SECTION = "references"  # 多场景 SKILL.md 末尾列出 reference/ 文件的章节
REF_DIR = "reference"
NOT_FOUND = "未找到根因"
SLUG_RE = re.compile(r"^[a-z0-9-]+$")

STEP_RE = re.compile(r"^##\s*步骤\s*(\d+)\s*[：:]\s*(.*)$", re.M)
# 「复用前置检查步骤 N」指前置检查，不是本文件的步骤
STEP_REF_RE = re.compile(r"(?<!前置检查)(?<!前置检查 )步骤\s*(\d+)")
ITEM_RE = re.compile(r"^(\d+)\.\s+\*\*", re.M)
INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
PARAM_RE = re.compile(r"<([^<>\s][^<>]*?)>")
HTML_TAGS = {"br", "br/", "b", "/b", "i", "/i", "p", "/p", "code", "/code", "sup", "sub"}
STEP_FIELDS = [(k, rf"\*\*{v}\*\*") for k, v in
               (("步骤名称", "步骤名称"), ("CLI 命令", r"CLI\s*命令"), ("跳转信息", "跳转信息"), ("根因定位", "根因定位"))]
SCENARIO_RE = re.compile(r"场景\s*[A-Za-z0-9]+\s*[：:].+")
# 跳转目标：「场景A：名称」或「场景A：名称（reference/xxx.md）」
TARGET_RE = re.compile(r"^(?P<name>.+?)\s*(?:[（(]\s*(?P<path>reference/[^）)\s]+)\s*[）)])?$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


class Linter:
    def __init__(self, files: dict[str, str]):
        self.files = files
        self.issues: list[dict] = []
        self.params_all: set[str] = set()
        self.params_required: set[str] = set()

    def add(self, level: str, file: str, msg: str) -> None:
        self.issues.append({"level": level, "file": file, "message": msg})

    # ------------------------------------------------------------ 入口

    def run(self) -> list[dict]:
        main = self.files.get(MAIN_FILE)
        if main is None:
            self.add("error", MAIN_FILE, "缺少主文件 SKILL.md")
            return self.issues
        self.check_frontmatter(MAIN_FILE, main)
        order, sections = split_sections(main)
        self.parse_params(sections.get("入参列表", ""))

        refs = sorted(p for p in self.files if p.startswith(f"{REF_DIR}/"))
        other = sorted(p for p in self.files if p != MAIN_FILE and not p.startswith(f"{REF_DIR}/"))
        multi = "场景跳转表" in sections.get("前置检查", "") or bool(refs)
        if other:
            self.add("warning", other[0], f"模板外的文件：{', '.join(other)}（多场景参考文件应放在 {REF_DIR}/ 下）")

        if multi:
            self.check_multi(order, sections, refs)
        else:
            self.check_single(order, sections)
        self.check_params(MAIN_FILE, sections)
        return dedupe(self.issues)

    # ------------------------------------------------------------ 公共

    def check_frontmatter(self, file: str, text: str, expect_name: str | None = None) -> None:
        meta = parse_frontmatter(text)
        if not meta:
            self.add("error", file, "缺少文档头部（--- name / description ---）")
            return
        name = meta.get("name", "")
        if not name:
            self.add("error", file, "文档头部缺少 name")
        elif not SLUG_RE.match(name):
            self.add("error", file, f"name「{name}」必须是英文 slug（^[a-z0-9-]+$）")
        elif expect_name and name != expect_name:
            self.add("error", file, f"name「{name}」应与文件名「{expect_name}」一致")
        if not meta.get("description"):
            self.add("error", file, "文档头部缺少 description")
        if "version" in meta and not VERSION_RE.match(meta["version"]):
            self.add("warning", file, f"version「{meta['version']}」应为 x.y.z 格式")

    def check_order(self, file: str, order: list[str], expected: list[str]) -> None:
        for s in expected:
            if s not in order:
                self.add("error", file, f"缺少一级章节「# {s}」")
        present = [s for s in order if s in expected]
        if present != [s for s in expected if s in present]:
            self.add("error", file, f"一级章节顺序应为 {' → '.join(expected)}，当前为 {' → '.join(present)}")

    def parse_params(self, text: str) -> None:
        for row in table_rows(text):
            if row and row[0]:
                key = norm_param(row[0])
                self.params_all.add(key)
                if len(row) > 1 and row[1].startswith("是"):
                    self.params_required.add(key)

    def precheck_items(self, file: str, text: str) -> list[int]:
        """检查前置检查 / 本场景采集的有序列表，返回序号。"""
        nums = [int(n) for n in ITEM_RE.findall(text)]
        if not nums:
            self.add("warning", file, "前置检查/采集块没有「1. **名称**」格式的条目")
            return nums
        if nums != list(range(1, len(nums) + 1)):
            self.add("warning", file, f"前置检查/采集块序号应从 1 连续，当前为 {nums}")
        if re.search(r"跳转(?:到)?\s*(?:前置检查)?步骤\s*\d+", text):
            self.add("error", file, "前置检查/采集块内不允许跳转（必须线性执行）")
        items = split_items(text)
        for n, body in items:
            if not re.search(r"CLI\s*命令", body):
                self.add("warning", file, f"前置检查/采集第 {n} 条缺少「CLI 命令」")
            if not re.search(r"采集内容", body):
                self.add("warning", file, f"前置检查/采集第 {n} 条缺少「采集内容」")
        return nums

    def check_collect_params(self, file: str, title: str, text: str) -> None:
        """前置检查/本场景采集只能用入参列表里的必填参数。"""
        for code in INLINE_CODE_RE.findall(text):
            for p in params_in(code):
                key = norm_param(p)
                if self.params_required and find_param(key, self.params_all) and not find_param(key, self.params_required):
                    self.add("error", file, f"{title}使用了非必填参数 <{p}>，只能从回显取得的参数应放进排查步骤")

    def check_steps(self, file: str, text: str) -> tuple[dict[str, int], int]:
        """检查 ## 步骤N，返回 {根因: 步骤号} 与最大步骤号。"""
        matches = list(STEP_RE.finditer(text))
        nums = [int(m.group(1)) for m in matches]
        if not nums:
            self.add("error", file, "没有「## 步骤N：名称」格式的排查步骤")
            return {}, 0
        if nums != list(range(1, len(nums) + 1)):
            self.add("error", file, f"步骤编号应从 1 开始连续，当前为 {nums}")
        max_step = max(nums)
        causes: dict[str, int] = {}
        for i, m in enumerate(matches):
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            chunk = text[m.end():end]
            n = int(m.group(1))
            for label, pattern in STEP_FIELDS:
                if not re.search(pattern, chunk):
                    self.add("warning", file, f"步骤{n} 缺少「{label}」")
            for ref in STEP_REF_RE.finditer(chunk):
                k = int(ref.group(1))
                if k < 1 or k > max_step:
                    self.add("error", file, f"步骤{n} 引用了不存在的步骤{k}")
            for c in root_causes_in_step(chunk):
                causes.setdefault(c, n)
        last = text[matches[-1].start():]
        if NOT_FOUND not in last:
            self.add("warning", file, f"最后一步（步骤{max_step}）应写明全部判据不命中时判定「{NOT_FOUND}」")
        return causes, max_step

    def check_cause_table(self, file: str, table_text: str, step_causes: dict[str, int], pre_causes: list[str]) -> None:
        rows = table_rows(table_text)
        names = [r[0].strip() for r in rows if r and r[0].strip()]
        for c, n in step_causes.items():
            if c not in names:
                self.add("error", file, f"步骤{n} 的根因「{c}」未出现在根因对照表中（需逐字一致）")
        for c in pre_causes:
            if c not in names:
                self.add("warning", file, f"采集阶段判定的根因「{c}」未出现在根因对照表中")
        known = set(step_causes) | set(pre_causes) | {NOT_FOUND}
        for c in names:
            if c not in known:
                self.add("warning", file, f"根因对照表中的「{c}」在排查步骤中没有对应的根因定位")
        if NOT_FOUND not in names:
            self.add("warning", file, f"根因对照表缺少「{NOT_FOUND}」一行")
        for r in rows:
            if len(r) < 4:
                self.add("warning", file, f"根因对照表行「{r[0] if r else ''}」不足 4 列")
            elif re.search(r"转向故障|转交", r[2]) and re.search(r"`[^`]*(?:转向故障|转交)", r[2]):
                self.add("warning", file, f"根因「{r[0]}」的转向不要写成反引号代码")

    def check_params(self, file: str, sections: dict[str, str]) -> None:
        for title, sec in sections.items():
            if title == "入参列表":
                continue
            self.check_param_names(file, title, sec)

    def check_param_names(self, file: str, title: str, text: str) -> None:
        for code in INLINE_CODE_RE.findall(text):
            if re.search(r"\{[^}]+\}|\bXXX\b", code):
                self.add("warning", file, f"「{title}」中 `{code}` 使用了 {{}} 或 XXX 占位符，应改用 <>")
            for p in params_in(code):
                if re.search(r"\s[0-9a-f]{6,}$", p):
                    self.add("error", file, f"参数 <{p}> 带有抽取哈希，应去掉哈希并规整分隔符")
                elif self.params_all and not find_param(norm_param(p), self.params_all):
                    self.add("warning", file, f"参数 <{p}> 不在 SKILL.md 的入参列表中")

    # ------------------------------------------------------------ 单故障

    def check_single(self, order: list[str], sections: dict[str, str]) -> None:
        f = MAIN_FILE
        self.check_order(f, order, SINGLE_SECTIONS)
        extra = [s for s in order if s not in SINGLE_SECTIONS]
        if extra:
            self.add("warning", f, f"存在模板外的一级标题：{', '.join(extra)}")

        pre_text = sections.get("前置检查", "")
        pre_list, jump = split_h2(pre_text, "步骤跳转表")
        pre_nums = self.precheck_items(f, pre_list)
        self.check_collect_params(f, "前置检查", pre_list)

        causes, max_step = self.check_steps(f, sections.get("排查步骤", "")) if "排查步骤" in sections else ({}, 0)

        if jump is None:
            self.add("warning", f, "前置检查之后缺少「## 步骤跳转表」")
        else:
            rows = table_rows(jump)
            if not rows:
                self.add("error", f, "步骤跳转表为空")
            self.check_jump_rows(f, rows, pre_nums)
            for r in rows:
                if len(r) >= 3:
                    m = re.search(r"步骤\s*(\d+)", r[2])
                    if not m:
                        self.add("error", f, f"步骤跳转表的跳转目标「{r[2]}」应写成「步骤 N：名称」")
                    elif int(m.group(1)) > max_step:
                        self.add("error", f, f"步骤跳转表指向不存在的步骤{m.group(1)}")

        if "根因对照表" in sections:
            self.check_cause_table(f, sections["根因对照表"], causes, precheck_causes(pre_list))

    def check_jump_rows(self, file: str, rows: list[list[str]], pre_nums: list[int]) -> None:
        seen: dict[str, str] = {}
        for r in rows:
            if len(r) < 3:
                self.add("error", file, f"跳转表行「{' | '.join(r)}」应有 3 列")
                continue
            m = re.search(r"步骤\s*(\d+)", r[0])
            if not m or int(m.group(1)) not in pre_nums:
                self.add("error", file, f"跳转表「前置检查步骤」列「{r[0]}」没有指向真实存在的前置检查序号")
            elif "`" not in r[0]:
                self.add("warning", file, f"跳转表「{r[0]}」应附上该步的命令")
            key = re.sub(r"\s+", "", r[1])
            target = split_target(r[2])[0]
            if key in seen and seen[key] != target:
                self.add("error", file, f"同一判据「{r[1]}」指向了两个目标")
            seen[key] = target

    # ------------------------------------------------------------ 多场景

    def check_multi(self, order: list[str], sections: dict[str, str], refs: list[str]) -> None:
        f = MAIN_FILE
        self.check_order(f, order, MULTI_MAIN_SECTIONS)
        if "根因对照表" in sections:
            self.add("error", f, "多场景时根因对照表应写在各场景参考文件中，不放在 SKILL.md")
        if REFS_SECTION in order and order[-1] != REFS_SECTION:
            self.add("warning", f, f"「# {REFS_SECTION}」应放在 SKILL.md 末尾")
        extra = [s for s in order if s not in SINGLE_SECTIONS and s != REFS_SECTION]
        if extra:
            self.add("warning", f, f"存在模板外的一级标题：{', '.join(extra)}")

        pre_list, jump = split_h2(sections.get("前置检查", ""), "场景跳转表")
        pre_nums = self.precheck_items(f, pre_list)
        self.check_collect_params(f, "前置检查", pre_list)

        jump_targets: list[tuple[str, str | None]] = []
        if jump is None:
            self.add("error", f, "多场景时前置检查之后必须有「## 场景跳转表」")
        else:
            rows = table_rows(jump)
            self.check_jump_rows(f, rows, pre_nums)
            jump_targets = [split_target(r[2]) for r in rows if len(r) >= 3]

        steps_sec = sections.get("排查步骤", "")
        if STEP_RE.search(steps_sec):
            self.add("error", f, "多场景时「# 排查步骤」只放参考文件表，步骤应写在 reference/ 的场景文件中")
        ref_table: dict[str, str] = {}
        for r in table_rows(steps_sec):
            if len(r) < 2:
                continue
            scen, path = clean_target(r[0]), r[1].strip().strip("`")
            if not re.fullmatch(rf"{REF_DIR}/[a-z0-9-]+\.md", path):
                self.add("error", f, f"参考文件「{path}」必须是 {REF_DIR}/<英文名>.md")
            elif re.fullmatch(r"scenario-?[a-z0-9]*|scene-?[a-z0-9]*|[a-z]?\d+", PurePosixPath(path).stem):
                self.add("error", f, f"参考文件名「{path}」应有语义，不能用场景编号")
            if path in ref_table.values():
                self.add("error", f, f"参考文件「{path}」被多个场景共用")
            ref_table[scen] = path
        if not ref_table:
            self.add("error", f, "「# 排查步骤」缺少「场景 | 参考文件 | 内容」参考文件表")

        for t, tpath in jump_targets:
            if t and t not in ref_table:
                self.add("error", f, f"场景跳转表的目标「{t}」不在参考文件表中（需逐字一致）")
            elif tpath and tpath != ref_table[t]:
                self.add("error", f, f"场景跳转表中「{t}」指向 {tpath}，与参考文件表中的 {ref_table[t]} 不一致")
        jump_names = {t for t, _ in jump_targets}
        for scen in ref_table:
            if jump_targets and scen not in jump_names:
                self.add("error", f, f"场景「{scen}」没有出现在场景跳转表中")
        if REFS_SECTION not in sections:
            self.add("warning", f, f"SKILL.md 末尾缺少「# {REFS_SECTION}」章节（列出 reference/ 下的全部文件）")
        else:
            listed_names = set(re.findall(r"^\s*[-*]\s+`?([\w.-]+\.md)`?\s*$", sections[REFS_SECTION], re.M))
            table_names = {PurePosixPath(p).name for p in ref_table.values()}
            for n in sorted(table_names - listed_names):
                self.add("warning", f, f"「# {REFS_SECTION}」中缺少 {n}")
            for n in sorted(listed_names - table_names):
                self.add("warning", f, f"「# {REFS_SECTION}」列出的 {n} 不在参考文件表中")
        listed = set(ref_table.values())
        for p in refs:
            if p not in listed:
                self.add("error", p, "该参考文件没有在 SKILL.md 的参考文件表中列出，读者到不了")
        for scen, path in ref_table.items():
            if path not in self.files:
                self.add("error", f, f"参考文件表列出的「{path}」不存在")
            else:
                self.check_ref_file(path, scen)

    def check_ref_file(self, path: str, scen: str) -> None:
        text = self.files[path]
        self.check_frontmatter(path, text, PurePosixPath(path).stem)
        order, sections = split_sections(text)
        if len(order) < 2 or not SCENARIO_RE.fullmatch(order[0]) or order[1:] != ["根因对照表"]:
            self.add("error", path, f"一级章节应为「# 场景X：名称」→「# 根因对照表」，当前为 {' → '.join(order) or '（无）'}")
        if order and SCENARIO_RE.fullmatch(order[0]) and clean_target(order[0]) != scen:
            self.add("error", path, f"标题「{order[0]}」与 SKILL.md 参考文件表中的「{scen}」不一致")
        body = sections.get(order[0], "") if order else ""
        first_step = STEP_RE.search(body)
        collect = body[: first_step.start()] if first_step else body
        pre_causes: list[str] = []
        if "本场景采集" in collect:
            self.precheck_items(path, collect)
            self.check_collect_params(path, "本场景采集", collect)
            pre_causes = precheck_causes(collect)
        causes, _ = self.check_steps(path, body)
        if "根因对照表" in sections:
            self.check_cause_table(path, sections["根因对照表"], causes, pre_causes)
        self.check_params(path, sections)


# ---------------------------------------------------------------- 工具函数

def lint_draft(files: dict[str, str], base: dict[str, str] | None = None) -> list[dict]:
    issues = Linter(files).run()
    if base:
        issues += check_preserved(files, base)
    return issues


def lint_changes(files: dict[str, str], base: dict[str, str] | None) -> tuple[list[dict], list[dict]]:
    """只看修改内容：返回 (本次修改引入的问题, 原版中已存在的问题)。

    用同一套规则分别检查原版和草稿，原版里已有的问题（同一文件、同一描述）不算本次修改引入的。
    没有原版（新建 skill）时，全部问题都算本次引入。
    """
    issues = Linter(files).run()
    if not base:
        return issues, []
    old = {(i["file"], i["message"]) for i in Linter(base).run()}
    new = [i for i in issues if (i["file"], i["message"]) not in old]
    existing = [i for i in issues if (i["file"], i["message"]) in old]
    return new + check_preserved(files, base), existing


MIN_PROSE_CHARS = 30


def prose_paragraphs(text: str) -> list[str]:
    """提取模板结构之外的说明性段落（执行规则、补充说明等），用于检查修改时是否被删改。

    跳过标题、表格、编号条目（步骤/前置检查条目，属于正常修改范围）及其缩进子项。
    """
    paras, cur = [], []

    def flush():
        if cur:
            p = norm_ws(" ".join(cur))
            if len(p) >= MIN_PROSE_CHARS:
                paras.append(p)
            cur.clear()

    in_fence = False
    body = re.sub(rf"^# {REFS_SECTION}\s*$.*?(?=^# |\Z)", "", strip_frontmatter(text), flags=re.M | re.S)
    for line in body.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            flush()
            continue
        if in_fence:
            continue
        st = line.strip()
        if not st or st.startswith(("#", "|")) or re.match(r"^\d+\.\s", st) or line[:1] in (" ", "\t"):
            flush()
            continue
        if re.match(r"^[-*]\s", st):  # 顶层列表项各自成段
            flush()
        cur.append(st)
    flush()
    return paras


def norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def check_preserved(files: dict[str, str], base: dict[str, str]) -> list[dict]:
    """原版中的说明性段落在草稿里找不到（被删除或改写）时给出提醒。"""
    draft_all = norm_ws("\n".join(files.values()))
    issues = []
    for path, text in base.items():
        if not path.endswith(".md"):
            continue
        for p in prose_paragraphs(text):
            if p not in draft_all:
                short = p if len(p) <= 60 else p[:60] + "…"
                issues.append({"level": "warning", "file": path, "message": f"原有说明段落被删除或改写，请确认：「{short}」"})
    return issues


def dedupe(issues: list[dict]) -> list[dict]:
    seen, out = set(), []
    for it in issues:
        key = (it["file"], it["message"])
        if key not in seen:
            seen.add(key)
            out.append(it)
    return out


def norm_param(s: str) -> str:
    return re.sub(r"[\s\-_]", "", s).lower()


def find_param(key: str, names: set[str]) -> bool:
    """参数名与入参列表匹配；允许 id 后缀差异（<color-id> ↔「color」）。"""
    if key in names:
        return True
    base = key[:-2] if key.endswith("id") else key
    return any(n == base or (n.endswith("id") and n[:-2] == base) for n in names)


def params_in(code: str) -> list[str]:
    return [p for p in PARAM_RE.findall(code) if p.lower() not in HTML_TAGS]


def strip_frontmatter(text: str) -> str:
    return re.sub(r"^﻿?---[ \t]*\n.*?\n---[ \t]*(?:\n|$)", "", text, count=1, flags=re.S)


def strip_code_fences(text: str) -> str:
    return re.sub(r"^\s*```.*?^\s*```[ \t]*$", "", text, flags=re.M | re.S)


def split_sections(text: str) -> tuple[list[str], dict[str, str]]:
    body = strip_code_fences(strip_frontmatter(text))
    order, content = [], {}
    matches = list(re.finditer(r"^# (.+?)\s*$", body, re.M))
    for i, m in enumerate(matches):
        title = m.group(1).strip()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        order.append(title)
        content[title] = body[m.end():end]
    return order, content


def split_h2(text: str, title: str) -> tuple[str, str | None]:
    """把前置检查拆成 (条目部分, 跳转表部分)。"""
    m = re.search(rf"^##\s*{title}\s*$", text, re.M)
    if not m:
        return text, None
    nxt = re.search(r"^##\s", text[m.end():], re.M)
    end = m.end() + nxt.start() if nxt else len(text)
    return text[: m.start()] + text[end:], text[m.end():end]


def split_items(text: str) -> list[tuple[int, str]]:
    ms = list(ITEM_RE.finditer(text))
    return [(int(m.group(1)), text[m.start(): ms[i + 1].start() if i + 1 < len(ms) else len(text)]) for i, m in enumerate(ms)]


def table_rows(text: str) -> list[list[str]]:
    """markdown 表格数据行（去掉表头与分隔行）。遇到第一个表格结束即停止。"""
    rows, started = [], False
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            if started:
                break
            continue
        started = True
        cells = [c.strip() for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue
        rows.append(cells)
    return rows[1:] if rows else []


def split_target(s: str) -> tuple[str, str | None]:
    """跳转目标 → (场景名, 参考文件路径或 None)。"""
    m = TARGET_RE.match(clean_target(s))
    return (m.group("name"), m.group("path")) if m else (clean_target(s), None)


def clean_target(s: str) -> str:
    s = s.replace("→", "").replace("**", "").replace("`", "").strip()
    return re.sub(r"\s*([：:])\s*", r"\1", s)


def root_causes_in_step(step_text: str) -> list[str]:
    names = []
    lines = step_text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^\s*(?:\d+\.|-)\s*\*\*根因定位\*\*\s*[：:]?\s*(.*)$", line)
        if not m:
            continue
        if m.group(1).strip():
            names.append(m.group(1).strip())
        for nxt in lines[i + 1:]:
            if re.match(r"^\s*\d+\.\s", nxt) or nxt.startswith("#"):
                break
            b = re.match(r"^\s+[-*]\s+(.*)$", nxt)
            if b:
                names.append(b.group(1).strip())
    out = []
    for n in names:
        n = re.sub(r"^\*\*(.*)\*\*$", r"\1", n.strip().rstrip("。.；;").strip())
        if n and n not in ("无", "-"):
            out.append(n)
    return out


def precheck_causes(text: str) -> list[str]:
    return [m.group(1).strip() for m in re.finditer(r"根因为\s*[\"“「]([^\"”」]+)[\"”」]", text)]
