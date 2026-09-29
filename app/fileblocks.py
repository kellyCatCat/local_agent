"""模型输出的多文件协议：

<<<FILE: SKILL.md>>>
...完整内容...
<<<END FILE>>>

<<<DELETE: references/old.md>>>
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

FILE_RE = re.compile(r"^<<<FILE:\s*(?P<path>[^>\n]+?)\s*>>>[ \t]*\n(?P<body>.*?)^<<<END FILE>>>[ \t]*$", re.M | re.S)
DELETE_RE = re.compile(r"^<<<DELETE:\s*(?P<path>[^>\n]+?)\s*>>>[ \t]*$", re.M)
# 未闭合的文件块（模型输出被截断）
OPEN_RE = re.compile(r"^<<<FILE:\s*(?P<path>[^>\n]+?)\s*>>>", re.M)


class UnsafePathError(ValueError):
    pass


def safe_relpath(path: str) -> str:
    """规范化 skill 目录内的相对路径，拒绝绝对路径与 .. 穿越。"""
    p = path.strip().strip("`'\"").replace("\\", "/")
    pp = PurePosixPath(p)
    if not p or pp.is_absolute() or ".." in pp.parts or p.startswith("~") or ":" in pp.parts[0]:
        raise UnsafePathError(f"非法文件路径：{path}")
    parts = [x for x in pp.parts if x not in ("", ".")]
    if not parts:
        raise UnsafePathError(f"非法文件路径：{path}")
    return "/".join(parts)


@dataclass
class ParsedOutput:
    files: dict[str, str] = field(default_factory=dict)
    deletes: list[str] = field(default_factory=list)
    truncated: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.files or self.deletes)


def _strip_outer_fence(body: str) -> str:
    """模型偶尔会在标记内再包一层 ``` 围栏，去掉它。"""
    lines = body.strip("\n").split("\n")
    if len(lines) >= 2 and re.match(r"^```[\w-]*\s*$", lines[0]) and lines[-1].strip() == "```":
        inner = "\n".join(lines[1:-1])
        # 仅在内部看起来就是 markdown 文档（frontmatter 或标题开头）时才剥离，避免误伤
        if inner.lstrip().startswith(("---", "#")):
            return inner
    return body.strip("\n")


def parse_output(text: str) -> ParsedOutput:
    out = ParsedOutput()
    closed_spans = []
    for m in FILE_RE.finditer(text):
        closed_spans.append(m.span())
        try:
            path = safe_relpath(m.group("path"))
        except UnsafePathError as e:
            out.errors.append(str(e))
            continue
        out.files[path] = _strip_outer_fence(m.group("body")) + "\n"
    for m in DELETE_RE.finditer(text):
        try:
            out.deletes.append(safe_relpath(m.group("path")))
        except UnsafePathError as e:
            out.errors.append(str(e))
    for m in OPEN_RE.finditer(text):
        if not any(s <= m.start() < e for s, e in closed_spans):
            out.truncated.append(m.group("path").strip())
    return out


def strip_blocks(text: str) -> str:
    """把文件块替换成简短占位，用于构造历史消息或在聊天里展示。"""
    text = FILE_RE.sub(lambda m: f"[已输出文件 {m.group('path').strip()}]", text)
    return text.strip()


def render_files(files: dict[str, str]) -> str:
    return "\n\n".join(f"<<<FILE: {p}>>>\n{c.rstrip()}\n<<<END FILE>>>" for p, c in sorted(files.items(), key=_order))


def _order(item):
    p = item[0]
    return (p != "SKILL.md", p)
