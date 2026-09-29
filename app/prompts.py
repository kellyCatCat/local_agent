"""构造发给模型的提示词。"""
from __future__ import annotations

import json
import re

from .fileblocks import render_files

MAX_RECOMMEND_SOURCE_CHARS = 8000


SYSTEM_TEMPLATE = """你是网络设备故障排查 skill 的编写专家。你根据用户提供的源文档（步骤表 / markdown，描述了若干子场景的诊断步骤），严格按照【skill 模板规范】和【本地标准补充】新建或修改 skill，并根据用户的追问继续修改。两者冲突时以【本地标准补充】为准。

# skill 模板规范

{template}

{conventions}

# 输出协议（必须遵守）

一个 skill 是一个目录：单故障只有 SKILL.md；覆盖多个故障场景时为 SKILL.md + reference/<语义英文名>.md（每个场景一个文件），布局以模板规范为准。
修改已有 skill 时，如果新增的子场景使它从单故障变成覆盖多个故障场景，按多场景布局重组（拆出 reference/ 文件）。

新增或修改文件时，每个文件按如下格式输出**完整内容**（不能省略、不能写"同上/其余不变"）：

<<<FILE: SKILL.md>>>
（文件完整内容）
<<<END FILE>>>

<<<FILE: reference/neighbor-down.md>>>
（文件完整内容）
<<<END FILE>>>

- 只输出本次新增或有改动的文件；没输出的文件保持原样。
- 需要删除文件时，单独一行输出：<<<DELETE: 相对路径>>>（例如重组后不再需要的参考文件）。
- 标记行必须顶格、独占一行，不要把文件块包在 ``` 代码围栏里。
- skill 的 name 与参考文件名必须是按语义取的英文 slug，不要拼音音译；用户指定了 name 时照用。
- 文件块之前用不超过 10 行的文字简要说明本次改动要点（单故障/多场景、新增/修改了哪些场景、步骤、根因）。
- 如果用户只是提问、不需要修改，直接回答，不要输出任何文件块。
- 源文档中的信息不足或存在矛盾时，在说明中明确指出，不要编造 CLI。
"""


RECOMMEND_PROMPT = """下面是本地 skill 库中已有的 skill 列表，以及用户新上传的源文档（补充子场景的诊断步骤）。
请判断：这些源文档应当合并进某个已有 skill（modify），还是应当新建一个 skill（create）。

判断依据：故障现象/告警/适用时机是否相同；若源文档只是已有 skill 故障现象下的新子场景、新根因或新排查分支，应 modify；若是不同的故障现象，应 create。

# 已有 skill
{skills}

# 源文档（可能已截断）
{sources}

只输出一个 JSON 对象，不要输出其他内容：
{{"action": "modify 或 create", "target": "要修改的 skill 目录名（create 时为 null）", "suggested_name": "新建时按语义取的英文 slug（^[a-z0-9-]+$，不要拼音音译；modify 时为 null）", "candidates": ["其他可能相关的已有 skill 目录名"], "reason": "一两句中文理由"}}
"""


def system_prompt(template: str, conventions: str = "") -> str:
    conv = f"# 本地标准补充\n\n{conventions.strip()}" if conventions.strip() else ""
    return SYSTEM_TEMPLATE.format(template=template.strip(), conventions=conv)


def sources_block(uploads: list[dict]) -> str:
    return "\n\n".join(f"## 源文档：{u['name']}\n\n{u['text'].strip()}" for u in uploads)


def recommend_prompt(skills: list[dict], uploads: list[dict]) -> str:
    if skills:
        lines = []
        for s in skills:
            heads = "；".join(h for h in s.get("headings", []) if h.startswith("步骤"))[:400]
            lines.append(f"- 目录名：{s['name']}\n  description：{s.get('description') or '（无）'}\n  步骤：{heads or '（无）'}")
        skills_text = "\n".join(lines)
    else:
        skills_text = "（skill 库为空）"
    src = sources_block(uploads)
    if len(src) > MAX_RECOMMEND_SOURCE_CHARS:
        src = src[:MAX_RECOMMEND_SOURCE_CHARS] + "\n……（已截断）"
    return RECOMMEND_PROMPT.format(skills=skills_text, sources=src)


def parse_recommendation(text: str, skill_names: list[str]) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    data = {}
    if m:
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            data = {}
    action = data.get("action") if data.get("action") in ("modify", "create") else None
    target = data.get("target") if data.get("target") in skill_names else None
    if action == "modify" and not target:
        action = "create"
    if not action:
        action = "create"
    return {
        "action": action,
        "target": target if action == "modify" else None,
        "suggested_name": data.get("suggested_name") or None,
        "candidates": [c for c in (data.get("candidates") or []) if c in skill_names and c != target],
        "reason": data.get("reason") or ("模型未给出有效建议，默认新建" if not m else ""),
    }


def first_turn(mode: str, target: str | None, base_files: dict[str, str], uploads: list[dict], request: str,
               name: str | None = None, example: tuple[str, dict[str, str]] | None = None) -> str:
    if mode == "modify":
        task = (
            f"# 任务\n修改已有 skill「{target}」：把源文档中的子场景诊断步骤补充/合并进去。"
            "保留原有正确内容，不要无故删除；模板结构之外的执行规则与说明段落原样保留；"
            "合并后保证步骤编号连续、跳转目标真实存在、根因对照表覆盖全部根因；"
            "若合并后覆盖多个故障场景，按多场景布局重组。"
            f"\n\n# 已有 skill 文件\n\n{render_files(base_files)}"
        )
    else:
        naming = f"skill 的 name 使用「{name}」。" if name else "skill 的 name 按语义取英文 slug（不要拼音音译）。"
        task = f"# 任务\n根据源文档新建一个 skill，按故障场景数量选择单故障或多场景布局。{naming}"
        if example:
            task += (
                f"\n\n# 参考样例：本地标准 skill「{example[0]}」（节选）\n\n"
                "只参考它的格式、写法和与具体故障无关的通用执行规则，不要照抄它的故障内容、命令和根因。\n\n"
                f"{render_files(example[1])}"
            )
    return f"{task}\n\n# 源文档\n\n{sources_block(uploads)}\n\n# 用户要求\n\n{request.strip() or '请按模板规范生成。'}"


def followup_turn(draft_files: dict[str, str], new_uploads: list[dict], request: str) -> str:
    parts = []
    if new_uploads:
        parts.append(f"# 新增源文档\n\n{sources_block(new_uploads)}")
    if draft_files:
        parts.append(f"# 当前 skill 最新版本（以此为准，用户可能手动编辑过）\n\n{render_files(draft_files)}")
    parts.append(f"# 用户要求\n\n{request.strip()}")
    return "\n\n".join(parts)
