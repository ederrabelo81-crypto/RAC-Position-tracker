"""Run Collection page — start/stop the scraper subprocess + live log."""

import re
import subprocess
import sys
import time
from pathlib import Path

import streamlit as st

from lib.state import _init_state

PROJECT_ROOT = Path(__file__).resolve().parent.parent

PLATFORMS = {
    "ml":              "Mercado Livre",
    "magalu":          "Magalu",
    "amazon":          "Amazon",
    "google_shopping": "Google Shopping",
    "leroy":           "Leroy Merlin",
    "dealers":         "Dealers (33 sites)",
}


def page_run_collection():
    st.title("🚀 Run Collection")
    st.caption("Select platforms and keywords, then start the scraping bot.")

    _init_state()

    col_left, col_right = st.columns([1, 1], gap="large")

    with col_left:
        st.subheader("Platforms")
        selected_platforms = []
        for key, label in PLATFORMS.items():
            if st.checkbox(label, value=True, key=f"plat_{key}"):
                selected_platforms.append(key)

        st.subheader("Pages per keyword")
        pages = st.slider("Pages", min_value=1, max_value=5, value=2)

        st.subheader("Options")
        headless = st.checkbox("Headless browser (recommended)", value=True)

    with col_right:
        st.subheader("Keywords")

        # Load keywords from config
        try:
            sys.path.insert(0, str(PROJECT_ROOT))
            from config import KEYWORDS_LIST
            kw_by_cat: dict = {}
            for kw in KEYWORDS_LIST:
                kw_by_cat.setdefault(kw.category, []).append(kw.term)
        except Exception:
            kw_by_cat = {"All": []}

        selected_keywords: list[str] = []
        for cat, terms in kw_by_cat.items():
            with st.expander(f"{cat} ({len(terms)})", expanded=False):
                for term in terms:
                    if st.checkbox(term, value=True, key=f"kw_{term}"):
                        selected_keywords.append(term)

    st.divider()

    # --- Start / Stop ---
    col_btn1, col_btn2, col_status = st.columns([1, 1, 4])

    with col_btn1:
        start = st.button(
            "▶ Start Collection",
            type="primary",
            disabled=st.session_state.running or not selected_platforms,
        )

    with col_btn2:
        stop = st.button(
            "⏹ Stop",
            disabled=not st.session_state.running,
        )

    with col_status:
        if st.session_state.running:
            _done  = st.session_state.get("tasks_done", 0)
            _total = st.session_state.get("total_tasks", 1)
            _pct   = min(_done / _total, 1.0) if _total > 0 else 0.0
            _elapsed = time.time() - (st.session_state.get("start_time") or time.time())
            if _pct > 0.01:
                _eta = (_elapsed / _pct) * (1 - _pct)
                _h, _rem = divmod(int(_eta), 3600)
                _m, _s   = divmod(_rem, 60)
                _eta_str = (f"~{_h}h {_m}m" if _h else f"~{_m}m {_s}s") + " remaining"
            else:
                _eta_str = "estimating…"
            _plat = st.session_state.get("current_platform", "")
            _kw   = st.session_state.get("current_keyword", "")
            _label = f"⏳ {int(_pct * 100)}%  ·  {_done}/{_total} tasks  ·  {_eta_str}"
            if _plat:
                _label += f"  ·  {_plat}"
            if _kw:
                _label += f"  →  {_kw[:50]}"
            st.progress(_pct, text=_label)
        elif st.session_state.run_done:
            st.success("✅ Collection completed.")

    # --- Handle start ---
    if start and not st.session_state.running and selected_platforms:
        cmd = [sys.executable, str(PROJECT_ROOT / "main.py")]
        cmd += ["--platforms"] + selected_platforms
        cmd += ["--pages", str(pages)]
        if selected_keywords:
            cmd += ["--keywords"] + selected_keywords
        if not headless:
            cmd += ["--no-headless"]

        # Compute total tasks for progress tracking:
        # dealers use their own site list; other platforms use keyword list.
        try:
            from scrapers.dealers import DEALER_CONFIGS as _DC
            _n_dealers = len(_DC)
        except Exception:
            _n_dealers = 13
        _n_kw = len(selected_keywords) if selected_keywords else sum(len(v) for v in kw_by_cat.values())
        _total = sum(_n_dealers if p == "dealers" else _n_kw for p in selected_platforms)

        st.session_state.process          = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(PROJECT_ROOT),
            bufsize=1,
        )
        st.session_state.running          = True
        st.session_state.run_done         = False
        st.session_state.log              = ""
        st.session_state.tasks_done       = 0
        st.session_state.total_tasks      = max(_total, 1)
        st.session_state.start_time       = time.time()
        st.session_state.current_platform = ""
        st.session_state.current_keyword  = ""
        st.rerun()

    # --- Handle stop ---
    if stop and st.session_state.process:
        st.session_state.process.terminate()
        st.session_state.running  = False
        st.session_state.run_done = False
        st.session_state.log     += "\n[Stopped by user]"
        st.rerun()

    # --- Live log ---
    st.subheader("Log")
    log_box = st.empty()

    if st.session_state.running and st.session_state.process:
        proc = st.session_state.process
        # Read up to 50 lines per rerun cycle and parse progress markers
        new_lines = []
        for _ in range(50):
            line = proc.stdout.readline()
            if not line:
                break
            new_lines.append(line)
            st.session_state.log += line

        for line in new_lines:
            if "Iniciando scraper:" in line:
                m = re.search(r"Iniciando scraper:\s*(.+)$", line.strip())
                if m:
                    st.session_state.current_platform = m.group(1).strip()
            elif "Iniciando keyword:" in line:
                st.session_state.tasks_done += 1
                m = re.search(r"Iniciando keyword:\s*'([^']+)'", line)
                if m:
                    st.session_state.current_keyword = m.group(1)
            elif "Coleta finalizada!" in line:
                st.session_state.tasks_done = st.session_state.total_tasks

        if proc.poll() is not None:
            remaining = proc.stdout.read()
            if remaining:
                st.session_state.log += remaining
            st.session_state.running          = False
            st.session_state.run_done         = True
            st.session_state.process          = None
            st.session_state.tasks_done       = st.session_state.total_tasks
            st.session_state.current_platform = ""
            st.session_state.current_keyword  = ""
        else:
            time.sleep(0.3)
            st.rerun()

    log_box.code(
        st.session_state.log[-4000:] if st.session_state.log else "No output yet.",
        language="bash",
    )
