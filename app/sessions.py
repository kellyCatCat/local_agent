"""会话持久化：每个会话一个 JSON 文件，刷新页面或重启服务后可继续追问。"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path

ID_RE = re.compile(r"^[0-9a-f]{32}$")


def new_session() -> dict:
    now = time.time()
    return {
        "id": uuid.uuid4().hex,
        "title": "新会话",
        "created_at": now,
        "updated_at": now,
        "uploads": [],          # [{id, name, size, text, sent}]
        "recommendation": None,  # 模型推荐结果
        "mode": None,           # "modify" | "create"
        "target": None,         # modify 时的 skill 目录名；写回后 create 也会变为 modify
        "name": None,           # create 时用户指定的 skill name（英文 slug）
        "base_files": {},       # 目标 skill 的原始文本文件快照（用于 diff / 计算删除）
        "messages": [],         # [{role, content, ts, llm, version}]
        "drafts": [],           # [{version, files, ts, source, note}]
        "writebacks": [],       # [{ts, name, backup}]
    }


def current_draft(s: dict) -> dict[str, str]:
    return dict(s["drafts"][-1]["files"]) if s["drafts"] else {}


def add_draft(s: dict, files: dict[str, str], source: str, note: str = "") -> int:
    version = (s["drafts"][-1]["version"] + 1) if s["drafts"] else 1
    s["drafts"].append({"version": version, "files": files, "ts": time.time(), "source": source, "note": note})
    return version


class SessionStore:
    def __init__(self, root: Path):
        self.root = root

    def _path(self, sid: str) -> Path:
        if not ID_RE.match(sid or ""):
            raise KeyError(sid)
        return self.root / f"{sid}.json"

    def create(self) -> dict:
        s = new_session()
        self.save(s)
        return s

    def get(self, sid: str) -> dict:
        p = self._path(sid)
        if not p.is_file():
            raise KeyError(sid)
        return json.loads(p.read_text("utf-8"))

    def save(self, s: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        s["updated_at"] = time.time()
        p = self._path(s["id"])
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(s, ensure_ascii=False, indent=1), "utf-8")
        os.replace(tmp, p)

    def delete(self, sid: str) -> None:
        p = self._path(sid)
        if p.is_file():
            p.unlink()

    def list(self) -> list[dict]:
        if not self.root.is_dir():
            return []
        out = []
        for p in self.root.glob("*.json"):
            try:
                s = json.loads(p.read_text("utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            out.append({
                "id": s["id"], "title": s.get("title", ""), "mode": s.get("mode"),
                "target": s.get("target"), "updated_at": s.get("updated_at", 0),
            })
        return sorted(out, key=lambda x: -x["updated_at"])
