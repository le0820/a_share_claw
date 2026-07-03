from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .utils import parse_bool


@dataclass(frozen=True)
class AppConfig:
    root_dir: Path
    data_dir: Path
    workspace_dir: Path
    database_path: Path
    agent_session_db_path: Path
    telegram_bot_token: str | None
    telegram_allowed_user_ids: frozenset[str]
    openai_model: str
    timezone: str
    scheduler_poll_seconds: int
    enable_bash: bool
    enable_file_write: bool
    enable_codex_tool: bool
    fake_ai: bool
    mcp_config_path: Path
    skill_dirs: tuple[Path, ...]
    plugin_dirs: tuple[Path, ...]
    max_tool_output_chars: int

    @classmethod
    def from_env(cls, root_dir: Path | None = None) -> "AppConfig":
        root = (root_dir or Path.cwd()).resolve()
        _load_dotenv(root / ".env")
        data_dir = _path_from_env("ASCLAW_DATA_DIR", root / "data", root)
        workspace_dir = _path_from_env("ASCLAW_WORKSPACE_DIR", root, root)
        database_path = data_dir / "a_share_claw.sqlite3"
        agent_session_db_path = data_dir / "agent_sessions.sqlite3"
        mcp_config_path = _path_from_env("ASCLAW_MCP_CONFIG", root / ".mcp.json", root)
        skill_dirs = _paths_from_env(
            "ASCLAW_SKILL_DIRS",
            (
                root / ".codex" / "skills",
                root / "skills",
                root / ".openclaw" / "skills",
                root / ".openclaw" / "workspace" / "skills",
                root / ".openclaw" / "workspace" / "repo_skills",
            ),
            root,
        )
        plugin_dirs = _paths_from_env(
            "ASCLAW_PLUGIN_DIRS",
            (
                root / ".codex" / "plugins",
                root / "plugins",
                root / ".openclaw" / "plugins",
            ),
            root,
        )
        return cls(
            root_dir=root,
            data_dir=data_dir,
            workspace_dir=workspace_dir,
            database_path=database_path,
            agent_session_db_path=agent_session_db_path,
            telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN") or None,
            telegram_allowed_user_ids=frozenset(_csv(os.environ.get("TELEGRAM_ALLOWED_USER_IDS"))),
            openai_model=os.environ.get("ASCLAW_OPENAI_MODEL", "gpt-5.5"),
            timezone=os.environ.get("ASCLAW_TIMEZONE", "America/Los_Angeles"),
            scheduler_poll_seconds=int(os.environ.get("ASCLAW_SCHEDULER_POLL_SECONDS", "15")),
            enable_bash=parse_bool(os.environ.get("ASCLAW_ENABLE_BASH"), False),
            enable_file_write=parse_bool(os.environ.get("ASCLAW_ENABLE_FILE_WRITE"), True),
            enable_codex_tool=parse_bool(os.environ.get("ASCLAW_ENABLE_CODEX_TOOL"), False),
            fake_ai=parse_bool(os.environ.get("ASCLAW_FAKE_AI"), False),
            mcp_config_path=mcp_config_path,
            skill_dirs=skill_dirs,
            plugin_dirs=plugin_dirs,
            max_tool_output_chars=int(os.environ.get("ASCLAW_MAX_TOOL_OUTPUT_CHARS", "12000")),
        )

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "users").mkdir(parents=True, exist_ok=True)
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

    def safe_dict(self) -> dict[str, object]:
        return {
            "root_dir": str(self.root_dir),
            "data_dir": str(self.data_dir),
            "workspace_dir": str(self.workspace_dir),
            "database_path": str(self.database_path),
            "agent_session_db_path": str(self.agent_session_db_path),
            "telegram_bot_token": bool(self.telegram_bot_token),
            "telegram_allowed_user_ids": sorted(self.telegram_allowed_user_ids),
            "openai_model": self.openai_model,
            "timezone": self.timezone,
            "scheduler_poll_seconds": self.scheduler_poll_seconds,
            "enable_bash": self.enable_bash,
            "enable_file_write": self.enable_file_write,
            "enable_codex_tool": self.enable_codex_tool,
            "fake_ai": self.fake_ai,
            "mcp_config_path": str(self.mcp_config_path),
            "skill_dirs": [str(path) for path in self.skill_dirs],
            "plugin_dirs": [str(path) for path in self.plugin_dirs],
        }


def _path_from_env(name: str, default: Path, root: Path) -> Path:
    raw = os.environ.get(name)
    if not raw:
        return default.resolve()
    path = Path(raw).expanduser()
    return (root / path).resolve() if not path.is_absolute() else path.resolve()


def _paths_from_env(name: str, defaults: tuple[Path, ...], root: Path) -> tuple[Path, ...]:
    raw = os.environ.get(name)
    if not raw:
        return tuple(path.resolve() for path in defaults)
    paths = []
    for item in raw.split(os.pathsep):
        if not item.strip():
            continue
        path = Path(item).expanduser()
        paths.append((root / path).resolve() if not path.is_absolute() else path.resolve())
    return tuple(paths)


def _csv(value: str | None) -> set[str]:
    if not value:
        return set()
    return {item.strip() for item in value.split(",") if item.strip()}


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = value.strip().strip('"').strip("'")
        os.environ[key] = value
