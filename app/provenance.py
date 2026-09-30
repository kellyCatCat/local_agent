"""CLI 来源检查：skill 中的命令必须能在源步骤表的命令来源列中找到出处。

模板规定：可用的命令来源只有步骤表的「命令行」「配置修复建议」「修复验证」「步骤详细描述」列，
「回显」列不是命令来源。目前只支持 Excel 步骤表（能按表头区分列）；其他格式的源文档不做此项检查。

只检查处在"命令位置"上的反引号内容：
- 「CLI 命令」行及其下一级子项（括号里的字段说明、执行条件不算）；
- 根因对照表的「修复CLI和方法」「复检命令」两列。
"""
from __future__ import annotations

import re

from .lint import table_rows

INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
# 命令：小写字母开头的词 + 空白 + 后续内容（排除 `Policy State`、`DOWN` 这类字段/取值）
CMD_RE = re.compile(r"^[a-z][\w-]*(\s+\S.*)?$")
PAREN_RE = re.compile(r"（[^（）]*）")
CLI_LINE_RE = re.compile(r"^(\s*)(?:\d+\.|[-*])\s*(?:\*\*)?CLI\s*命令")
# 单个词的命令只认这些（其余单词多为字段/取值，如 `ospfNbrStateChange`）
SINGLE_WORD_CMDS = {"commit", "quit", "return", "save", "system-view"}


def norm_cmd(s: str) -> str:
    """比对用的规范化：去掉参数中的抽取哈希，参数只保留字母数字，统一 {} 为 <>，压缩空白，小写。"""
    s = re.sub(r"\{([^{}]+)\}", r"<\1>", s)
    s = re.sub(r"<([^<>]+?)\s+[0-9a-f]{6,}>", r"<\1>", s)
    s = re.sub(r"<([^<>]+)>", lambda m: "<" + re.sub(r"[\s_-]", "", m.group(1)).lower() + ">", s)
    s = s.replace("<br>", " ")
    return re.sub(r"\s+", " ", s).strip().lower()


def _is_cmd(code: str) -> bool:
    code = code.strip()
    if not CMD_RE.match(code):
        return False
    return " " in code or code in SINGLE_WORD_CMDS


def _cli_line_text(text: str) -> list[str]:
    """「CLI 命令」行及其更深缩进的子项。"""
    out, cli_indent = [], None
    for line in text.splitlines():
        m = CLI_LINE_RE.match(line)
        if m:
            cli_indent = len(m.group(1))
            out.append(line)
            continue
        if cli_indent is not None:
            sub = re.match(r"^(\s*)[-*]\s", line)
            if sub and len(sub.group(1)) > cli_indent:
                out.append(line)
                continue
            if line.strip():
                cli_indent = None
    return out


def _fix_columns(text: str) -> list[str]:
    """根因对照表的「修复CLI和方法」「复检命令」两列。"""
    m = re.search(r"^# 根因对照表\s*$(.*?)(?=^# |\Z)", text, re.M | re.S)
    if not m:
        return []
    return [cell for row in table_rows(m.group(1)) for cell in row[2:4]]


def extract_commands(files: dict[str, str]) -> dict[str, set[str]]:
    """{命令原文: {出现的文件}}"""
    found: dict[str, set[str]] = {}
    for path, text in files.items():
        if not path.endswith(".md"):
            continue
        spans = [PAREN_RE.sub("", l) for l in _cli_line_text(text)] + _fix_columns(text)
        for span in spans:
            for code in INLINE_CODE_RE.findall(span):
                if _is_cmd(code):
                    found.setdefault(code.strip(), set()).add(path)
    return found


def check_cli_sources(files: dict[str, str], uploads: list[dict], base: dict[str, str] | None = None) -> tuple[list[dict], str]:
    """返回 (问题列表, 说明)。base 非空时只检查新增/改动的命令。"""
    checkable = [u for u in uploads if u.get("cli")]
    skipped = [u["name"] for u in uploads if not u.get("cli")]
    if not checkable:
        if not uploads:
            return [], "未上传源文档，未做 CLI 来源检查"
        return [], "CLI 来源检查目前只支持 Excel 步骤表（需有「命令行」等表头），本会话的源文档未做此项检查"
    src = norm_cmd("\n".join(u["cli"]["cmd"] for u in checkable))
    echo = norm_cmd("\n".join(u["cli"]["echo"] for u in checkable))
    old = {norm_cmd(c) for c in extract_commands(base or {})}
    # 有非 Excel 源文档时，命令可能来自那些文档，降为提醒
    level = "warning" if skipped else "error"
    issues = []
    for cmd, paths in sorted(extract_commands(files).items()):
        n = norm_cmd(cmd)
        if n in old or n in src:
            continue
        where = "只出现在「回显」列，回显不是命令来源" if n in echo else "在源步骤表的命令来源列中找不到出处"
        for p in sorted(paths):
            issues.append({"level": level, "file": p, "message": f"CLI `{cmd}` {where}"})
    note = f"CLI 来源检查依据：{'、'.join(u['name'] for u in checkable)}"
    if skipped:
        note += f"；未纳入检查（非 Excel 步骤表）：{'、'.join(skipped)}，相关问题降为提醒"
    return issues, note
