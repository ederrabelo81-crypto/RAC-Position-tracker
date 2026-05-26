"""Global filter renderer + session-state accessors."""

from datetime import date, timedelta

import streamlit as st

from lib.state import _load_presets, _save_presets
from lib.supabase import get_filter_options


def _render_global_filters() -> None:
    """Render persistent global filters in the sidebar."""
    opts = get_filter_options()
    with st.expander("🌐 Filtros Globais", expanded=False):
        st.date_input(
            "Período",
            value=(date.today() - timedelta(days=7), date.today()),
            max_value=date.today(),
            format="DD/MM/YYYY",
            key="gf_dates",
        )
        st.multiselect(
            "Plataformas", opts["platforms"],
            placeholder="Selecione plataformas…",
            key="gf_platforms",
        )
        st.multiselect(
            "Marcas", opts["brands"],
            placeholder="Selecione marcas…",
            key="gf_brands",
        )
        st.checkbox("Comparar período anterior", key="gf_compare")

        if st.session_state.get("gf_compare"):
            st.date_input(
                "Período de comparação",
                value=(date.today() - timedelta(days=14), date.today() - timedelta(days=8)),
                max_value=date.today(),
                format="DD/MM/YYYY",
                key="gf_cmp_dates",
            )

        # Preset save / load
        st.caption("Presets")
        presets = _load_presets()
        preset_name = st.text_input(
            "Nome do preset",
            placeholder="Ex: Midea - 7 dias",
            key="gf_preset_name",
            label_visibility="collapsed",
        )
        if st.button("💾 Salvar preset", key="gf_save_preset",
                     use_container_width=True, help="Salvar filtros atuais como preset"):
            if preset_name:
                gf = st.session_state.get("gf_dates", ())
                presets[preset_name] = {
                    "start":     str(gf[0]) if gf else str(date.today() - timedelta(days=7)),
                    "end":       str(gf[1]) if len(gf) > 1 else str(date.today()),
                    "platforms": st.session_state.get("gf_platforms", []),
                    "brands":    st.session_state.get("gf_brands", []),
                }
                _save_presets(presets)
                st.success(f"Salvo: '{preset_name}'")
            else:
                st.warning("Digite um nome para o preset.")

        if presets:
            sel = st.selectbox(
                "Carregar preset",
                ["— selecione —"] + list(presets.keys()),
                key="gf_load_preset",
                label_visibility="collapsed",
            )
            if sel and sel != "— selecione —" and sel != st.session_state.get("_last_loaded_preset"):
                st.session_state["_last_loaded_preset"] = sel
                p = presets[sel]
                try:
                    st.session_state["gf_dates"]     = (date.fromisoformat(p["start"]), date.fromisoformat(p["end"]))
                    st.session_state["gf_platforms"] = p.get("platforms", [])
                    st.session_state["gf_brands"]    = p.get("brands", [])
                    st.rerun()
                except Exception:
                    pass


def _gf_dates() -> tuple:
    gf = st.session_state.get("gf_dates", ())
    if len(gf) >= 2:
        return gf[0], gf[1]
    return date.today() - timedelta(days=7), date.today()


def _gf_platforms() -> list:
    return list(st.session_state.get("gf_platforms", []))


def _gf_brands() -> list:
    return list(st.session_state.get("gf_brands", []))


def _gf_compare() -> bool:
    return bool(st.session_state.get("gf_compare", False))


def _gf_cmp_dates() -> tuple:
    gf = st.session_state.get("gf_cmp_dates", ())
    if len(gf) >= 2:
        return gf[0], gf[1]
    return date.today() - timedelta(days=14), date.today() - timedelta(days=8)
