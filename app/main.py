"""Skill 维护 Agent：FastAPI 后端 + 静态前端。"""
from __future__ import annotations

import asyncio
import difflib
import json
import time
import uuid
from urllib.parse import quote

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import llm, prompts
from .config import ROOT, settings
from .fileblocks import UnsafePathError, parse_output, safe_relpath, strip_blocks
from .lint import lint_draft
from .parser import ParseError, parse_upload
from .sessions import SessionStore, add_draft, current_draft
from .versioning import apply_bumps, plan_bumps
from .skills import MAIN_FILE, SkillStore, parse_frontmatter, valid_name, zip_files

app = FastAPI(title="Skill 维护 Agent")
skills = SkillStore(settings.skills_dir, settings.backups_dir)
sessions = SessionStore(settings.sessions_dir)
_locks: dict[str, asyncio.Lock] = {}

MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def _lock(sid: str) -> asyncio.Lock:
    return _locks.setdefault(sid, asyncio.Lock())


def _session(sid: str) -> dict:
    try:
        return sessions.get(sid)
    except KeyError:
        raise HTTPException(404, "会话不存在")


def _template() -> str:
    try:
        return settings.template_path.read_text("utf-8")
    except OSError:
        raise HTTPException(500, f"找不到 skill 模板：{settings.template_path}")


def _conventions() -> str:
    try:
        return settings.conventions_path.read_text("utf-8")
    except OSError:
        return ""


MAX_EXAMPLE_CHARS = 30000


def _example_skill(s: dict) -> tuple[str, dict[str, str]] | None:
    """新建 skill 时给模型的格式样例：优先推荐里的相关 skill，其次库中的多场景 skill。

    只取 SKILL.md 和一个 reference 文件，控制提示长度。
    """
    lib = skills.list()
    names = [x["name"] for x in lib]
    rec = s.get("recommendation") or {}
    multi = [x["name"] for x in lib if any(f.startswith("reference/") for f in x["files"])]
    for name in (rec.get("candidates") or []) + multi + names:
        if name not in names:
            continue
        files = skills.read_files(name)
        if MAIN_FILE not in files:
            continue
        picked = {MAIN_FILE: files[MAIN_FILE]}
        refs = sorted(p for p in files if p.startswith("reference/"))
        if refs:
            picked[refs[0]] = files[refs[0]]
        if sum(map(len, picked.values())) <= MAX_EXAMPLE_CHARS:
            return name, picked
    return None


def _library_files(s: dict, files: dict[str, str]) -> dict[str, str]:
    """写回目标在库中的当前内容（用于计算 version 递增）。"""
    name = _draft_name(s, files)
    if not name or not skills.exists(name):
        return {}
    try:
        return skills.read_files(name)
    except (ValueError, FileNotFoundError):
        return {}


def _draft_name(s: dict, files: dict[str, str]) -> str | None:
    if s["mode"] == "modify":
        return s["target"]
    return s.get("name") or parse_frontmatter(files.get(MAIN_FILE, "")).get("name") or None


def _view(s: dict) -> dict:
    """返回给前端的会话视图。"""
    files = current_draft(s)
    return {
        **{k: s.get(k) for k in ("id", "title", "created_at", "updated_at", "recommendation", "mode", "target", "name", "writebacks")},
        "uploads": [{k: u[k] for k in ("id", "name", "size", "sent")} | {"chars": len(u["text"])} for u in s["uploads"]],
        "messages": [{k: m.get(k) for k in ("role", "content", "ts", "version", "error")} for m in s["messages"]],
        "versions": [{k: d[k] for k in ("version", "ts", "source", "note")} for d in s["drafts"]],
        "draft": {
            "version": s["drafts"][-1]["version"] if s["drafts"] else None,
            "files": files,
            "name": _draft_name(s, files),
            "lint": lint_draft(files, s["base_files"]) if files else [],
            "changed": _changed_files(s["base_files"], files),
            "version_bumps": plan_bumps(files, _library_files(s, files)) if files else [],
        },
        "base_files": list(s["base_files"]),
    }


def _changed_files(base: dict[str, str], draft: dict[str, str]) -> dict[str, str]:
    out = {}
    for p in sorted(set(base) | set(draft)):
        if p not in base:
            out[p] = "added"
        elif p not in draft:
            out[p] = "deleted"
        elif base[p] != draft[p]:
            out[p] = "modified"
    return out


# ---------------------------------------------------------------- 配置与 skill 库

@app.get("/api/config")
def get_config():
    return {
        "model": settings.llm_model,
        "llm_configured": bool(settings.llm_base_url and settings.llm_model),
        "skills_dir": str(settings.skills_dir),
        "template_path": str(settings.template_path),
    }


@app.get("/api/skills")
def list_skills():
    return skills.list()


@app.get("/api/skills/{name}")
def get_skill(name: str):
    try:
        return {"name": name, "files": skills.read_files(name)}
    except (ValueError, FileNotFoundError):
        raise HTTPException(404, "skill 不存在")


def _zip_response(data: bytes, filename: str) -> Response:
    return Response(data, media_type="application/zip", headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"
    })


@app.get("/api/skills/{name}/download")
def download_skill(name: str):
    if not skills.exists(name):
        raise HTTPException(404, "skill 不存在")
    return _zip_response(skills.zip_skill(name), f"{name}.zip")


@app.get("/api/library/download")
def download_library():
    return _zip_response(skills.zip_all(), f"skills-{time.strftime('%Y%m%d-%H%M%S')}.zip")


# ---------------------------------------------------------------- 会话

@app.get("/api/sessions")
def list_sessions():
    return sessions.list()


@app.post("/api/sessions")
def create_session():
    return _view(sessions.create())


@app.get("/api/sessions/{sid}")
def get_session(sid: str):
    return _view(_session(sid))


@app.delete("/api/sessions/{sid}")
def delete_session(sid: str):
    try:
        sessions.delete(sid)
    except KeyError:
        raise HTTPException(404, "会话不存在")
    return {"ok": True}


@app.post("/api/sessions/{sid}/uploads")
async def upload(sid: str, files: list[UploadFile] = File(...)):
    async with _lock(sid):
        s = _session(sid)
        errors = []
        for f in files:
            data = await f.read()
            if len(data) > MAX_UPLOAD_BYTES:
                errors.append(f"{f.filename}：文件过大")
                continue
            try:
                text = parse_upload(f.filename or "", data)
            except ParseError as e:
                errors.append(f"{f.filename}：{e}")
                continue
            s["uploads"].append({"id": uuid.uuid4().hex[:8], "name": f.filename, "size": len(data), "text": text, "sent": False})
        if s["title"] == "新会话" and s["uploads"]:
            s["title"] = s["uploads"][0]["name"]
        sessions.save(s)
        return {"session": _view(s), "errors": errors}


@app.get("/api/sessions/{sid}/uploads/{uid}")
def get_upload(sid: str, uid: str):
    for u in _session(sid)["uploads"]:
        if u["id"] == uid:
            return {"name": u["name"], "text": u["text"]}
    raise HTTPException(404, "文件不存在")


@app.delete("/api/sessions/{sid}/uploads/{uid}")
async def delete_upload(sid: str, uid: str):
    async with _lock(sid):
        s = _session(sid)
        s["uploads"] = [u for u in s["uploads"] if u["id"] != uid]
        sessions.save(s)
        return _view(s)


@app.post("/api/sessions/{sid}/recommend")
async def recommend(sid: str):
    s = _session(sid)
    if not s["uploads"]:
        raise HTTPException(400, "请先上传源文档")
    lib = skills.list()
    msgs = [
        {"role": "system", "content": "你是 skill 库管理助手，只输出 JSON。"},
        {"role": "user", "content": prompts.recommend_prompt(lib, s["uploads"])},
    ]
    try:
        text = await llm.complete(msgs)
    except llm.LLMError as e:
        raise HTTPException(502, str(e))
    rec = prompts.parse_recommendation(text, [x["name"] for x in lib])
    async with _lock(sid):
        s = _session(sid)
        s["recommendation"] = rec
        sessions.save(s)
        return _view(s)


class TargetReq(BaseModel):
    mode: str
    target: str | None = None
    name: str | None = None  # create 时由用户给出的英文 slug（可选）


@app.post("/api/sessions/{sid}/target")
async def set_target(sid: str, req: TargetReq):
    if req.mode not in ("modify", "create"):
        raise HTTPException(400, "mode 只能是 modify 或 create")
    async with _lock(sid):
        s = _session(sid)
        if s["messages"]:
            raise HTTPException(409, "会话已开始生成，不能再切换目标；请新建会话")
        if req.mode == "modify":
            if not req.target or not skills.exists(req.target):
                raise HTTPException(404, "要修改的 skill 不存在")
            s["base_files"] = skills.read_files(req.target)
            s["target"] = req.target
            s["name"] = None
        else:
            name = (req.name or "").strip()
            if name and not valid_name(name):
                raise HTTPException(400, f"name「{name}」必须是英文 slug（小写字母、数字、短横线）")
            if name and skills.exists(name):
                raise HTTPException(409, f"skill「{name}」已存在，请选择「修改」或换一个名称")
            s["base_files"], s["target"], s["name"] = {}, None, name or None
        s["mode"] = req.mode
        s["drafts"] = []
        if s["base_files"]:
            add_draft(s, dict(s["base_files"]), "base", f"原始版本：{req.target}")
        sessions.save(s)
        return _view(s)


class ChatReq(BaseModel):
    message: str = ""


def _llm_messages(s: dict, user_content: str) -> list[dict]:
    msgs = [{"role": "system", "content": prompts.system_prompt(_template(), _conventions())}]
    for m in s["messages"]:
        if m.get("error"):
            continue
        if m["role"] == "user":
            msgs.append({"role": "user", "content": m.get("llm") or m["content"]})
        else:
            # 历史回复中的文件内容已由"当前最新版本"取代，省略以节省上下文
            msgs.append({"role": "assistant", "content": strip_blocks(m["content"]) or "（已输出文件）"})
    msgs.append({"role": "user", "content": user_content})
    return msgs


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


@app.post("/api/sessions/{sid}/chat")
async def chat(sid: str, req: ChatReq):
    s = _session(sid)
    if not s["mode"]:
        raise HTTPException(400, "请先选择「修改已有 skill」或「新增 skill」")
    lock = _lock(sid)
    if lock.locked():
        raise HTTPException(409, "该会话正在生成中，请稍候")

    first = not any(m["role"] == "assistant" and not m.get("error") for m in s["messages"])
    new_uploads = [u for u in s["uploads"] if not u["sent"]]
    if first:
        if not s["uploads"]:
            raise HTTPException(400, "请先上传源文档")
        user_content = prompts.first_turn(
            s["mode"], s["target"], s["base_files"], s["uploads"], req.message, s.get("name"),
            _example_skill(s) if s["mode"] == "create" else None,
        )
        stored_llm = user_content
    else:
        if not req.message.strip():
            raise HTTPException(400, "请输入内容")
        user_content = prompts.followup_turn(current_draft(s), new_uploads, req.message)
        # 历史里只保留新增源文档和用户要求，草稿每轮都会以最新版本重新附上
        stored_llm = prompts.followup_turn({}, new_uploads, req.message)
    msgs = _llm_messages(s, user_content)
    display = req.message.strip() or "请按模板规范生成。"

    async def gen():
        async with lock:
            yield _sse({"type": "start"})
            parts: list[str] = []
            try:
                async for delta in llm.stream(msgs):
                    parts.append(delta)
                    yield _sse({"type": "delta", "text": delta})
            except llm.LLMError as e:
                yield _sse({"type": "error", "message": str(e)})
                return
            reply = "".join(parts)
            s2 = sessions.get(sid)
            now = time.time()
            s2["messages"].append({"role": "user", "content": display, "llm": stored_llm, "ts": now})
            for u in s2["uploads"]:
                u["sent"] = True
            parsed = parse_output(reply)
            version = None
            if parsed.changed:
                files = current_draft(s2)
                for p in parsed.deletes:
                    files.pop(p, None)
                files.update(parsed.files)
                version = add_draft(s2, files, "model", ", ".join(list(parsed.files) + [f"-{p}" for p in parsed.deletes]))
            s2["messages"].append({"role": "assistant", "content": reply, "ts": time.time(), "version": version})
            sessions.save(s2)
            yield _sse({
                "type": "done",
                "version": version,
                "truncated": parsed.truncated,
                "errors": parsed.errors,
                "session": _view(s2),
            })

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class DraftReq(BaseModel):
    files: dict[str, str]
    note: str = "手动编辑"


@app.put("/api/sessions/{sid}/draft")
async def save_draft(sid: str, req: DraftReq):
    async with _lock(sid):
        s = _session(sid)
        if not s["mode"]:
            raise HTTPException(400, "请先选择修改目标")
        try:
            files = {safe_relpath(p): c for p, c in req.files.items()}
        except UnsafePathError as e:
            raise HTTPException(400, str(e))
        add_draft(s, files, "manual", req.note)
        sessions.save(s)
        return _view(s)


class RevertReq(BaseModel):
    version: int


@app.post("/api/sessions/{sid}/draft/revert")
async def revert_draft(sid: str, req: RevertReq):
    async with _lock(sid):
        s = _session(sid)
        for d in s["drafts"]:
            if d["version"] == req.version:
                add_draft(s, dict(d["files"]), "revert", f"回退到 v{req.version}")
                sessions.save(s)
                return _view(s)
        raise HTTPException(404, "版本不存在")


@app.get("/api/sessions/{sid}/draft/version/{version}")
def get_version(sid: str, version: int):
    for d in _session(sid)["drafts"]:
        if d["version"] == version:
            return d
    raise HTTPException(404, "版本不存在")


@app.get("/api/sessions/{sid}/diff")
def diff(sid: str, against: str = "base", version: int | None = None):
    """当前草稿（或指定版本）与原始 skill（against=base）或上一版本（against=prev）的差异。"""
    s = _session(sid)
    if not s["drafts"]:
        return {"files": []}
    idx = len(s["drafts"]) - 1
    if version is not None:
        idx = next((i for i, d in enumerate(s["drafts"]) if d["version"] == version), idx)
    new = s["drafts"][idx]["files"]
    old = s["base_files"] if against == "base" else (s["drafts"][idx - 1]["files"] if idx > 0 else {})
    out = []
    for p in sorted(set(old) | set(new), key=lambda x: (x != MAIN_FILE, x)):
        a, b = old.get(p), new.get(p)
        if a == b:
            continue
        lines = list(difflib.unified_diff((a or "").splitlines(), (b or "").splitlines(),
                                          f"原版/{p}", f"新版/{p}", n=3, lineterm=""))
        out.append({"path": p, "status": "added" if a is None else "deleted" if b is None else "modified", "diff": lines})
    return {"files": out}


@app.get("/api/sessions/{sid}/draft/download")
def download_draft(sid: str):
    s = _session(sid)
    files = current_draft(s)
    if not files:
        raise HTTPException(400, "还没有生成的 skill")
    if s["mode"] == "modify":
        # 带上目录中未参与生成的非文本文件（图片等）
        name = s["target"]
        deletes = [p for p in s["base_files"] if p not in files]
        data = skills.zip_skill(name, overlay=files, deletes=deletes)
    else:
        name = _draft_name(s, files)
        name = name if valid_name(name or "") else "new-skill"
        data = zip_files(name, files)
    return _zip_response(data, f"{name}.zip")


class WritebackReq(BaseModel):
    overwrite: bool = False


@app.post("/api/sessions/{sid}/writeback")
async def writeback(sid: str, req: WritebackReq):
    async with _lock(sid):
        s = _session(sid)
        files = current_draft(s)
        if MAIN_FILE not in files:
            raise HTTPException(400, "草稿中没有 SKILL.md，无法写回")
        name = _draft_name(s, files)
        if not name or (s["mode"] == "create" and not valid_name(name)):
            raise HTTPException(400, f"SKILL.md 中的 name「{name or ''}」不是合法的英文 kebab-case 名称")
        if s["mode"] == "create" and skills.exists(name) and not req.overwrite:
            raise HTTPException(409, f"skill「{name}」已存在，确认覆盖吗？（会先自动备份）")
        # create 覆盖已有 skill 时整体替换（旧文件已备份）；modify 时删除草稿里去掉的文件
        old = skills.read_files(name) if s["mode"] == "create" and skills.exists(name) else s["base_files"]
        deletes = [p for p in old if p not in files]
        # version 以库中当前版本为基准自动递增，并同步成一个新的草稿版本
        bumps = plan_bumps(files, _library_files(s, files))
        if bumps:
            files = apply_bumps(files, bumps)
            add_draft(s, files, "writeback", "写回：" + "，".join(f"{b['path']} {b['new']}" for b in bumps))
        backup = skills.write(name, files, deletes)
        s["writebacks"].append({"ts": time.time(), "name": name, "backup": backup, "version": s["drafts"][-1]["version"], "bumps": bumps})
        # 写回后，后续修改以写回的版本为基准
        s["mode"], s["target"] = "modify", name
        s["base_files"] = skills.read_files(name)
        sessions.save(s)
        return {"session": _view(s), "name": name, "backup": backup, "bumps": bumps}


# ---------------------------------------------------------------- 前端

app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")
