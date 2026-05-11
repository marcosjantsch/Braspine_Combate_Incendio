# -*- coding: utf-8 -*-
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

from core.config import BASE_DIR

API_CACHE_DIR = BASE_DIR / "data" / "cache"


def _is_inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def clean_api_cache() -> Dict[str, object]:
    cache_dir = API_CACHE_DIR.resolve()
    base_dir = BASE_DIR.resolve()
    if not _is_inside(cache_dir, base_dir):
        return {
            "ok": False,
            "path": str(cache_dir),
            "files_removed": 0,
            "dirs_removed": 0,
            "bytes_removed": 0,
            "message": "Limpeza bloqueada: pasta de cache fora da base do sistema.",
            "cleaned_at": datetime.now(timezone.utc).isoformat(),
        }

    cache_dir.mkdir(parents=True, exist_ok=True)
    files_removed = 0
    dirs_removed = 0
    bytes_removed = 0
    errors: list[str] = []

    for item in cache_dir.iterdir():
        try:
            if item.is_dir():
                for file_path in item.rglob("*"):
                    if file_path.is_file():
                        try:
                            bytes_removed += file_path.stat().st_size
                            files_removed += 1
                        except OSError:
                            pass
                shutil.rmtree(item)
                dirs_removed += 1
            elif item.is_file():
                bytes_removed += item.stat().st_size
                item.unlink()
                files_removed += 1
        except Exception as exc:
            errors.append(f"{item.name}: {exc}")

    message = (
        f"Cache de APIs limpo: {files_removed} arquivo(s), {dirs_removed} pasta(s), "
        f"{bytes_removed / 1024 / 1024:.2f} MB removidos."
    )
    if errors:
        message += f" Pendencias: {'; '.join(errors[:3])}"

    return {
        "ok": not errors,
        "path": str(cache_dir),
        "files_removed": files_removed,
        "dirs_removed": dirs_removed,
        "bytes_removed": bytes_removed,
        "message": message,
        "errors": errors,
        "cleaned_at": datetime.now(timezone.utc).isoformat(),
    }


def ensure_api_cache_cleaned_for_session(session_state) -> Dict[str, object] | None:
    if session_state.get("api_cache_cleaned_for_session"):
        return session_state.get("api_cache_cleanup_summary")
    summary = clean_api_cache()
    session_state["api_cache_cleaned_for_session"] = True
    session_state["api_cache_cleanup_summary"] = summary
    return summary
