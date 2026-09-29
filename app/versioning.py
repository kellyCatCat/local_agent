"""写回时自动递增文档头部的 version（x.y.z 的修订号加一）。

规则：
- 以库中原版本（base）为准：有改动的文件，version = base 的 version 修订号 + 1；
- SKILL.md 代表整个 skill，目录中任一文件有改动（含删除）就递增；
- 草稿里的 version 已经被手动改得比 base 高时，保留草稿的值；
- base 中没有该文件（新增文件、全新 skill）时不递增，保持草稿的值。
"""
from __future__ import annotations

import re

from .skills import MAIN_FILE

FM_RE = re.compile(r"^(﻿?---[ \t]*\n)(.*?)(\n---[ \t]*(?:\n|$))", re.S)
VERSION_LINE_RE = re.compile(r"^version:[ \t]*(.*?)[ \t]*$", re.M)
SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def get_version(text: str) -> str | None:
    m = FM_RE.match(text)
    if not m:
        return None
    v = VERSION_LINE_RE.search(m.group(2))
    return v.group(1).strip().strip("'\"") if v else None


def set_version(text: str, version: str) -> str:
    m = FM_RE.match(text)
    if not m:
        return text
    fm = m.group(2)
    if VERSION_LINE_RE.search(fm):
        fm = VERSION_LINE_RE.sub(f"version: {version}", fm, count=1)
    else:
        fm = fm.rstrip("\n") + f"\nversion: {version}"
    return m.group(1) + fm + m.group(3) + text[m.end():]


def _parse(v: str | None) -> tuple[int, int, int] | None:
    m = SEMVER_RE.match(v or "")
    return tuple(int(x) for x in m.groups()) if m else None


def _strip_version(text: str) -> str:
    return set_version(text, "") if get_version(text) is not None else text


def plan_bumps(files: dict[str, str], base: dict[str, str]) -> list[dict]:
    """计算写回时要做的 version 变更：[{path, old, new}]。"""
    if not base:
        return []
    changed = {p for p in files if p in base and _strip_version(files[p]) != _strip_version(base[p])}
    any_change = bool(changed) or any(p not in base for p in files) or any(p not in files for p in base)
    targets = set(changed)
    if any_change and MAIN_FILE in files and MAIN_FILE in base:
        targets.add(MAIN_FILE)
    plans = []
    for p in sorted(targets, key=lambda x: (x != MAIN_FILE, x)):
        old = _parse(get_version(base[p]))
        if not old:
            continue
        cur = _parse(get_version(files[p]))
        if cur and cur > old:
            continue  # 已手动提升
        new = f"{old[0]}.{old[1]}.{old[2] + 1}"
        if get_version(files[p]) != new:
            plans.append({"path": p, "old": get_version(files[p]) or "（无）", "new": new})
    return plans


def apply_bumps(files: dict[str, str], plans: list[dict]) -> dict[str, str]:
    out = dict(files)
    for pl in plans:
        out[pl["path"]] = set_version(out[pl["path"]], pl["new"])
    return out
