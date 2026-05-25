from __future__ import annotations

import os
from pathlib import Path
from typing import Any

ENV_PATH = Path(__file__).parent.parent / ".env"

SECRET_KEYS = {
    "ANTHROPIC_API_KEY", "ATLASSIAN_API_TOKEN", "FIGMA_API_TOKEN",
    "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN",
}
PLAIN_KEYS = {"ATLASSIAN_DOMAIN", "ATLASSIAN_EMAIL", "GOOGLE_CLIENT_ID"}
ALL_KEYS = SECRET_KEYS | PLAIN_KEYS


def _parse_env_file() -> dict[str, str]:
    if not ENV_PATH.exists():
        return {}
    out: dict[str, str] = {}
    for raw in ENV_PATH.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _write_env_file(values: dict[str, str]) -> None:
    """Preserve comments and order of existing lines; append new keys at the end."""
    existing_lines: list[str] = []
    if ENV_PATH.exists():
        existing_lines = ENV_PATH.read_text().splitlines()

    written: set[str] = set()
    new_lines: list[str] = []
    for raw in existing_lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            new_lines.append(raw)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in values:
            new_lines.append(f"{key}={values[key]}")
            written.add(key)
        else:
            new_lines.append(raw)

    for k, v in values.items():
        if k not in written:
            new_lines.append(f"{k}={v}")

    ENV_PATH.write_text("\n".join(new_lines) + "\n")


def _preview(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "•" * len(value)
    return f"{value[:4]}…{value[-4:]}"


def sync_env_from_disk() -> None:
    """Pull .env values into os.environ so the running server picks up changes
    made outside the UI (e.g. you edit .env in your editor). Empty values in the
    file don't overwrite a non-empty live value."""
    file_values = _parse_env_file()
    for k, v in file_values.items():
        if k not in ALL_KEYS:
            continue
        if v.strip():
            os.environ[k] = v
        elif not os.environ.get(k, "").strip():
            # nothing usable anywhere — make sure the key exists as empty
            os.environ[k] = ""


def get_status() -> dict[str, Any]:
    """Reflect current persisted state. Reloads .env from disk first so external
    edits are visible (and applied to the live process)."""
    sync_env_from_disk()
    file_values = _parse_env_file()
    live = {k: os.environ[k] for k in ALL_KEYS if os.environ.get(k, "").strip()}
    merged = {**file_values, **live}

    out: dict[str, Any] = {}
    for k in PLAIN_KEYS:
        out[k] = merged.get(k, "")
    for k in SECRET_KEYS:
        val = merged.get(k, "")
        out[k] = {"set": bool(val), "preview": _preview(val)}
    return out


def update(updates: dict[str, str]) -> dict[str, Any]:
    """Apply non-empty updates: write to .env file AND update os.environ for live use.
    Empty / missing values are ignored (= keep existing). Unknown keys are silently dropped."""
    file_values = _parse_env_file()
    merged = dict(file_values)
    for k, v in updates.items():
        if k not in ALL_KEYS:
            continue
        if v is None:
            continue
        v = v.strip()
        if v == "":
            continue
        merged[k] = v
        os.environ[k] = v

    _write_env_file(merged)
    return get_status()
