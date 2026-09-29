"""本地 skill 库：每个 skill 一个目录，主文件 SKILL.md。"""
from __future__ import annotations

import io
import re
import shutil
import time
import zipfile
from pathlib import Path

from .fileblocks import safe_relpath

MAIN_FILE = "SKILL.md"
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
# 作为文本读入、交给模型/界面的文件类型；其余文件（图片等）原样保留，不参与生成
TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".json", ".yaml", ".yml", ".py", ".sh", ".csv", ".toml", ".ini", ""}
MAX_TEXT_BYTES = 512 * 1024


def parse_frontmatter(text: str) -> dict[str, str]:
    m = re.match(r"^﻿?---[ \t]*\n(.*?)\n---[ \t]*(?:\n|$)", text, re.S)
    if not m:
        return {}
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith((" ", "\t")):
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip().strip("'\"")
    return meta


def valid_name(name: str) -> bool:
    return bool(NAME_RE.match(name or ""))


def zip_files(name: str, files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, content in files.items():
            zf.writestr(f"{name}/{safe_relpath(rel)}", content)
    return buf.getvalue()


class SkillStore:
    def __init__(self, root: Path, backups: Path):
        self.root = root
        self.backups = backups

    def _dir(self, name: str) -> Path:
        if not valid_name(name) and not (self.root / name).is_dir():
            raise ValueError(f"非法 skill 名称：{name}")
        d = (self.root / name).resolve()
        if d.parent != self.root.resolve():
            raise ValueError(f"非法 skill 名称：{name}")
        return d

    def exists(self, name: str) -> bool:
        try:
            return (self._dir(name) / MAIN_FILE).is_file()
        except ValueError:
            return False

    def list(self) -> list[dict]:
        if not self.root.is_dir():
            return []
        items = []
        for d in sorted(self.root.iterdir()):
            main = d / MAIN_FILE
            if d.name.startswith(".") or not main.is_file():
                continue
            text = main.read_text("utf-8", "replace")
            meta = parse_frontmatter(text)
            items.append({
                "name": d.name,
                "title": meta.get("name", d.name),
                "description": meta.get("description", ""),
                "headings": re.findall(r"^#{1,2} (.+)$", text, re.M)[:30],
                "files": sorted(str(p.relative_to(d)).replace("\\", "/") for p in d.rglob("*") if p.is_file()),
                "updated": main.stat().st_mtime,
            })
        return items

    def read_files(self, name: str) -> dict[str, str]:
        """读取 skill 目录中的文本文件，{相对路径: 内容}。"""
        d = self._dir(name)
        if not d.is_dir():
            raise FileNotFoundError(name)
        files = {}
        for p in sorted(d.rglob("*")):
            rel = str(p.relative_to(d)).replace("\\", "/")
            if not p.is_file() or any(part.startswith(".") for part in rel.split("/")):
                continue
            if p.suffix.lower() in TEXT_SUFFIXES and p.stat().st_size <= MAX_TEXT_BYTES:
                files[rel] = p.read_text("utf-8", "replace")
        return files

    def backup(self, name: str) -> str | None:
        d = self._dir(name)
        if not d.is_dir():
            return None
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dest = self.backups / name / stamp
        i = 1
        while dest.exists():
            dest = self.backups / name / f"{stamp}-{i}"
            i += 1
        shutil.copytree(d, dest)
        return str(dest)

    def write(self, name: str, files: dict[str, str], deletes: list[str] | None = None) -> str | None:
        """写回 skill（先整目录备份）。未出现在 files 中的已有文件保持不变。返回备份路径。"""
        d = self._dir(name)
        backup = self.backup(name)
        d.mkdir(parents=True, exist_ok=True)
        for rel in deletes or []:
            p = d / safe_relpath(rel)
            if p.is_file():
                p.unlink()
        for rel, content in files.items():
            p = d / safe_relpath(rel)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, "utf-8")
        return backup

    def zip_skill(self, name: str, overlay: dict[str, str] | None = None, deletes: list[str] | None = None) -> bytes:
        """打包 skill 目录；overlay 中的文件覆盖（或新增）到包中。"""
        buf = io.BytesIO()
        overlay = overlay or {}
        skip = set(deletes or []) | set(overlay)
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            try:
                d = self._dir(name)
            except ValueError:
                d = None
            if d and d.is_dir():
                for p in sorted(d.rglob("*")):
                    rel = str(p.relative_to(d)).replace("\\", "/")
                    if p.is_file() and rel not in skip:
                        zf.write(p, f"{name}/{rel}")
            for rel, content in overlay.items():
                zf.writestr(f"{name}/{rel}", content)
        return buf.getvalue()

    def zip_all(self) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for item in self.list():
                d = self.root / item["name"]
                for p in sorted(d.rglob("*")):
                    if p.is_file():
                        zf.write(p, f"{item['name']}/{str(p.relative_to(d)).replace(chr(92), '/')}")
        return buf.getvalue()
