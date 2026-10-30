"""
Small config file loader. Reads Ollama connection/model settings and a
default session output directory from a hand-editable config.yaml at
the project root, creating a sensible default one on first run so
there's always something to start from.

Nested schema (matches the actual config.yaml, not a flat one) --
sections are deep-merged against defaults one level at a time, so a
config.yaml that only overrides e.g. `ollama.model` still gets sensible
values for every other field without needing everything spelled out.
"""

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import yaml

DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"

DEFAULTS = {
    "ollama": {
        "model": "llama3.1",
        "host": "http://localhost:11434",
        "temperature": 0.1,
        "max_retries": 3,
    },
    "session": {
        "sessions_dir": "./sessions",
    },
}


@dataclass
class OllamaSettings:
    model: str
    host: str
    temperature: float
    max_retries: int


@dataclass
class SessionSettings:
    sessions_dir: str


@dataclass
class AppConfig:
    ollama: OllamaSettings
    session: SessionSettings


def _merge_with_defaults(data: dict, defaults: dict) -> dict:
    merged = {}
    for section, section_defaults in defaults.items():
        section_data = data.get(section) or {}
        merged[section] = {**section_defaults, **section_data}
    return merged


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> AppConfig:
    if not path.exists():
        path.write_text(yaml.dump(DEFAULTS, default_flow_style=False, sort_keys=False), encoding="utf-8")
        data = DEFAULTS
    else:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        data = _merge_with_defaults(raw, DEFAULTS)

    return AppConfig(
        ollama=OllamaSettings(**data["ollama"]),
        session=SessionSettings(**data["session"]),
    )
