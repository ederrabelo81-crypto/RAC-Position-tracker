"""Streamlit session-state defaults + filter-preset persistence."""

import json
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_FILTER_PRESETS_FILE = PROJECT_ROOT / "filter_presets.json"


def _init_state():
    defaults = {
        "process":          None,
        "running":          False,
        "log":              "",
        "run_done":         False,
        "tasks_done":       0,
        "total_tasks":      1,
        "start_time":       None,
        "current_platform": "",
        "current_keyword":  "",
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def _load_presets() -> dict:
    try:
        if _FILTER_PRESETS_FILE.exists():
            return json.loads(_FILTER_PRESETS_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_presets(presets: dict) -> None:
    try:
        _FILTER_PRESETS_FILE.write_text(
            json.dumps(presets, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    except Exception:
        pass
