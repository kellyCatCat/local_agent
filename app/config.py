"""从 .env / 环境变量读取配置。模型地址与密钥只放在 .env，不进代码仓。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _path(name: str, default: str) -> Path:
    p = Path(os.getenv(name) or default).expanduser()
    return p if p.is_absolute() else (ROOT / p).resolve()


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name) or default)
    except ValueError:
        return default


@dataclass
class Settings:
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    llm_temperature: float
    llm_timeout: float
    llm_max_tokens: int | None
    skills_dir: Path
    template_path: Path
    conventions_path: Path
    data_dir: Path
    host: str
    port: int

    @property
    def sessions_dir(self) -> Path:
        return self.data_dir / "sessions"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"


def load_settings() -> Settings:
    max_tokens = (os.getenv("LLM_MAX_TOKENS") or "").strip()
    return Settings(
        llm_base_url=(os.getenv("LLM_BASE_URL") or "").rstrip("/"),
        llm_api_key=os.getenv("LLM_API_KEY") or "",
        llm_model=os.getenv("LLM_MODEL") or "",
        llm_temperature=_float("LLM_TEMPERATURE", 0.2),
        llm_timeout=_float("LLM_TIMEOUT", 600),
        llm_max_tokens=int(max_tokens) if max_tokens.isdigit() else None,
        skills_dir=_path("SKILLS_DIR", "./skills"),
        template_path=_path("TEMPLATE_PATH", "./templates/skill_template.md"),
        conventions_path=_path("CONVENTIONS_PATH", "./templates/skill_conventions.md"),
        data_dir=_path("DATA_DIR", "./data"),
        host=os.getenv("HOST") or "127.0.0.1",
        port=int(os.getenv("PORT") or 8000),
    )


settings = load_settings()
