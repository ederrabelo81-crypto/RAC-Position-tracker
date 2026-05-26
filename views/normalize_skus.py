"""Normalize SKUs page — re-apply RAC name/brand/platform normalization in Supabase."""

import streamlit as st

from lib.supabase import get_filter_options


def page_normalize_skus():
    st.title("🔤 Normalize SKUs")
    st.caption(
        "Re-applies the RAC normalization rules to every `produto` field stored in Supabase. "
        "Only rows whose name actually changes are written back. "
        "Records without a recognized brand or BTU value are left untouched."
    )

    st.info(
        "**Format:** `Ar Condicionado {Marca} {Linha} {BTUs} {Tipo} {Ciclo} [{Forma}] [{Cor}]`\n\n"
        "Run a **Scan** first to preview which records would change, then **Apply** to write the updates."
    )

    col1, col2 = st.columns(2)

    with col1:
        scan_btn = st.button("🔍 Scan for outdated names", use_container_width=True)
    with col2:
        apply_btn = st.button(
            "✏️ Apply normalization",
            type="primary",
            use_container_width=True,
            help="Updates only rows whose normalized name differs from the stored value.",
        )

    # ── Scan (dry-run) ──
    if scan_btn:
        with st.spinner("Scanning Supabase… this may take a moment for large datasets."):
            from utils.supabase_client import normalize_all_products_in_supabase
            result = normalize_all_products_in_supabase(dry_run=True, preview_limit=30)
        st.session_state["norm_scan"] = result

    if "norm_scan" in st.session_state:
        r = st.session_state["norm_scan"]
        c1, c2, c3 = st.columns(3)
        c1.metric("Records scanned",     f"{r['scanned']:,}")
        c2.metric("Need update",         f"{r['changed']:,}",
                  delta=f"-{r['changed']:,}" if r["changed"] else None,
                  delta_color="inverse")
        c3.metric("Already normalized",  f"{r['unchanged']:,}")

        if r["changed"] == 0:
            st.success("✅ All product names are already normalized. Nothing to do.")
        else:
            pct = r["changed"] / r["scanned"] * 100 if r["scanned"] else 0
            st.warning(
                f"**{r['changed']:,}** records ({pct:.1f}%) have outdated names. "
                "Click **Apply normalization** to update them."
            )
            if r.get("preview"):
                with st.expander(f"Preview of changes ({len(r['preview'])} examples)", expanded=True):
                    rows = [
                        {"ID": ex["id"], "Before": ex["before"], "After": ex["after"]}
                        for ex in r["preview"]
                    ]
                    st.dataframe(rows, use_container_width=True)

    # ── Apply ──
    if apply_btn:
        scan = st.session_state.get("norm_scan")
        if not scan or scan["changed"] == 0:
            st.warning("Run a scan first and confirm there are records to update.")
        else:
            with st.spinner(f"Updating {scan['changed']:,} records…"):
                from utils.supabase_client import normalize_all_products_in_supabase
                result = normalize_all_products_in_supabase(dry_run=False)

            upd  = result["updated"]
            ded  = result.get("deduped", 0)
            errs = result["errors"]
            if errs == 0:
                st.success(
                    f"✅ Done. **{upd:,}** records renamed, "
                    f"**{ded:,}** duplicate old-name records removed."
                )
            else:
                st.warning(
                    f"Partial run: {upd:,} renamed, {ded:,} duplicates removed, "
                    f"{errs:,} with errors. Check Supabase logs."
                )
            if "norm_scan" in st.session_state:
                del st.session_state["norm_scan"]

    st.divider()

    # ── Brand Normalization ───────────────────────────────────────────────────
    st.subheader("🏷️ Brand Normalization")
    st.caption(
        "Unifies brand variants stored in the `marca` column. "
        '"Springer Midea", "Midea Carrier", and "Springer" are all Midea products — '
        "this consolidates them under a single `Midea` entry for cleaner analysis."
    )
    st.info(
        "| Variant in DB | → Canonical |\n|---|---|\n"
        "| Springer Midea | **Midea** |\n"
        "| Midea Carrier | **Midea** |\n"
        "| Springer | **Midea** |\n"
        "| Britania | **Britânia** |"
    )

    col_b1, col_b2 = st.columns(2)
    with col_b1:
        brand_scan_btn = st.button(
            "🔍 Scan brand variants",
            use_container_width=True,
            key="brand_scan_btn",
        )
    with col_b2:
        brand_apply_btn = st.button(
            "✏️ Apply brand normalization",
            type="primary",
            use_container_width=True,
            key="brand_apply_btn",
            help="Updates marca for all variant rows to the canonical name.",
        )

    if brand_scan_btn:
        with st.spinner("Scanning brand variants…"):
            from utils.supabase_client import normalize_brands_in_supabase
            brand_result = normalize_brands_in_supabase(dry_run=True)
        st.session_state["brand_scan"] = brand_result

    if "brand_scan" in st.session_state:
        br = st.session_state["brand_scan"]
        total_variants = sum(
            v["count"] for v in br["by_brand"].values() if v["count"] > 0
        )
        rows = [
            {
                "Variant (DB)": src,
                "→ Canonical": info["target"],
                "Records": info["count"] if info["count"] >= 0 else "error",
            }
            for src, info in br["by_brand"].items()
        ]
        st.dataframe(rows, use_container_width=True, hide_index=True)

        if total_variants == 0:
            st.success("✅ All brand names are already normalized!")
        else:
            st.warning(
                f"Found **{total_variants:,}** records with non-canonical brand names. "
                "Click **Apply brand normalization** to consolidate them."
            )

    if brand_apply_btn:
        scan = st.session_state.get("brand_scan")
        total_to_fix = (
            sum(v["count"] for v in scan["by_brand"].values() if v["count"] > 0)
            if scan else 0
        )
        if not scan or total_to_fix == 0:
            st.warning("Run a scan first to confirm there are records to update.")
        else:
            with st.spinner(f"Normalizing {total_to_fix:,} brand records…"):
                from utils.supabase_client import normalize_brands_in_supabase
                brand_result = normalize_brands_in_supabase(dry_run=False)
            if brand_result["errors"] == 0:
                st.success(
                    f"✅ Done. **{brand_result['total_updated']:,}** records updated."
                )
            else:
                st.warning(
                    f"Partial run: {brand_result['total_updated']:,} updated, "
                    f"{brand_result['errors']:,} with errors."
                )
            if "brand_scan" in st.session_state:
                del st.session_state["brand_scan"]
            # Clear cached filter options so the brand dropdown refreshes
            get_filter_options.clear()

    st.divider()

    # ── Re-extrair marcas Desconhecidas ───────────────────────────────────────
    st.subheader("🔄 Recalcular Marcas Desconhecidas")
    st.caption(
        "Varre registros com `marca = 'Desconhecida'` e re-aplica `extract_brand()` "
        "usando a lista atual de marcas em `config.BRANDS`. "
        "Use após adicionar novas marcas para recuperar registros históricos."
    )
    new_brands_list = [
        "AIWA", "American Range", "Geminis", "Fontaine", "Luxor",
        "Turbro", "Velleman", "Whynter", "DeLonghi", "Kian", "Equation",
    ]
    st.info(
        "Marcas recém-adicionadas (Abril 2026): "
        + ", ".join(f"**{b}**" for b in new_brands_list)
    )

    col_rb1, col_rb2 = st.columns(2)
    with col_rb1:
        rebrand_scan_btn = st.button(
            "🔍 Scan 'Desconhecida' records",
            use_container_width=True,
            key="rebrand_scan_btn",
        )
    with col_rb2:
        rebrand_apply_btn = st.button(
            "✏️ Apply brand recalculation",
            type="primary",
            use_container_width=True,
            key="rebrand_apply_btn",
            help="Atualiza o campo marca para todos os registros identificados.",
        )

    if rebrand_scan_btn:
        with st.spinner("Scanning 'Desconhecida' records…"):
            from utils.supabase_client import recalculate_unknown_brands_in_supabase
            rebrand_result = recalculate_unknown_brands_in_supabase(dry_run=True)
        st.session_state["rebrand_scan"] = rebrand_result

    if "rebrand_scan" in st.session_state:
        rb = st.session_state["rebrand_scan"]
        st.metric("Registros escaneados", f"{rb['scanned']:,}")
        col_m1, col_m2, col_m3 = st.columns(3)
        col_m1.metric("Identificados", f"{rb['scanned'] - rb['unchanged']:,}")
        col_m2.metric("Permanecem desconhecidos", f"{rb['unchanged']:,}")
        col_m3.metric("Erros", f"{rb.get('errors', 0):,}")

        if rb["preview"]:
            st.dataframe(
                rb["preview"],
                use_container_width=True,
                hide_index=True,
                column_config={
                    "id": st.column_config.NumberColumn("ID", width="small"),
                    "produto": st.column_config.TextColumn("Produto", width="large"),
                    "nova_marca": st.column_config.TextColumn("Nova Marca", width="medium"),
                },
            )

        if rb["scanned"] - rb["unchanged"] == 0:
            st.success("✅ Nenhum registro 'Desconhecida' identificado com as marcas atuais.")
        else:
            st.warning(
                f"**{rb['scanned'] - rb['unchanged']:,}** registros podem ser atualizados. "
                "Clique em **Apply brand recalculation** para gravar."
            )

    if rebrand_apply_btn:
        scan = st.session_state.get("rebrand_scan")
        to_fix = (scan["scanned"] - scan["unchanged"]) if scan else 0
        if not scan or to_fix == 0:
            st.warning("Execute o scan primeiro para confirmar os registros a atualizar.")
        else:
            with st.spinner(f"Atualizando {to_fix:,} registros…"):
                from utils.supabase_client import recalculate_unknown_brands_in_supabase
                rebrand_result = recalculate_unknown_brands_in_supabase(dry_run=False)
            if rebrand_result["errors"] == 0:
                st.success(
                    f"✅ Concluído. **{rebrand_result['updated']:,}** registros atualizados."
                )
            else:
                st.warning(
                    f"Parcial: {rebrand_result['updated']:,} atualizados, "
                    f"{rebrand_result['errors']:,} com erros."
                )
            if "rebrand_scan" in st.session_state:
                del st.session_state["rebrand_scan"]
            get_filter_options.clear()

    st.divider()

    # ── Platform / Seller Normalization ──────────────────────────────────────
    st.subheader("🏪 Platform / Seller Normalization")
    st.caption(
        "Corrige typos e capitalização nos campos `plataforma` e `seller`. "
        "Aplica em ambas as colunas simultaneamente."
    )
    st.info(
        "| Variant in DB | → Canonical |\n|---|---|\n"
        "| FerreiraCoasta | **FerreiraCosta** |\n"
        "| Webcontinental | **WebContinental** |"
    )

    col_p1, col_p2 = st.columns(2)
    with col_p1:
        plat_scan_btn = st.button(
            "🔍 Scan platform/seller variants",
            use_container_width=True,
            key="plat_scan_btn",
        )
    with col_p2:
        plat_apply_btn = st.button(
            "✏️ Apply platform/seller normalization",
            type="primary",
            use_container_width=True,
            key="plat_apply_btn",
            help="Updates plataforma and seller columns for all variant rows.",
        )

    if plat_scan_btn:
        with st.spinner("Scanning platform/seller variants…"):
            from utils.supabase_client import normalize_platforms_sellers_in_supabase
            plat_result = normalize_platforms_sellers_in_supabase(dry_run=True)
        st.session_state["plat_scan"] = plat_result

    if "plat_scan" in st.session_state:
        pr = st.session_state["plat_scan"]
        total_variants = sum(
            (v.get("plataforma") or 0) + (v.get("seller") or 0)
            for v in pr["by_mapping"].values()
            if isinstance(v.get("plataforma"), int) and isinstance(v.get("seller"), int)
        )
        rows = [
            {
                "Variant (DB)": src,
                "→ Canonical":   info["target"],
                "plataforma":    info.get("plataforma", "?"),
                "seller":        info.get("seller", "?"),
            }
            for src, info in pr["by_mapping"].items()
        ]
        st.dataframe(rows, use_container_width=True, hide_index=True)

        if total_variants == 0:
            st.success("✅ All platform/seller names are already correct!")
        else:
            st.warning(
                f"Found **{total_variants:,}** records with non-canonical "
                "platform/seller names. Click **Apply** to fix them."
            )

    if plat_apply_btn:
        scan = st.session_state.get("plat_scan")
        total_to_fix = 0
        if scan:
            for v in scan["by_mapping"].values():
                p = v.get("plataforma") or 0
                s = v.get("seller") or 0
                if isinstance(p, int):
                    total_to_fix += p
                if isinstance(s, int):
                    total_to_fix += s
        if not scan or total_to_fix == 0:
            st.warning("Run a scan first to confirm there are records to update.")
        else:
            with st.spinner(f"Normalizing {total_to_fix:,} records…"):
                from utils.supabase_client import normalize_platforms_sellers_in_supabase
                plat_result = normalize_platforms_sellers_in_supabase(dry_run=False)
            if plat_result["errors"] == 0:
                st.success(
                    f"✅ Done. **{plat_result['total_updated']:,}** records updated."
                )
            else:
                st.warning(
                    f"Partial run: {plat_result['total_updated']:,} updated, "
                    f"{plat_result['errors']:,} with errors."
                )
            if "plat_scan" in st.session_state:
                del st.session_state["plat_scan"]
            get_filter_options.clear()

    st.divider()
    st.subheader("Normalization rules")
    st.markdown(
        "| Component | Rule |\n"
        "|-----------|------|\n"
        "| **Marca** | Aliases unified (Springer Midea → Midea, TCL Semp → TCL, …) |\n"
        "| **Linha** | Preserved exactly per brand — each model line stays distinct for phase-out tracking |\n"
        "| **BTUs** | Brazilian format: 12.000 BTUs, 9.000 BTUs, … |\n"
        "| **Tipo** | `Inverter` (default) or `On/Off` |\n"
        "| **Ciclo** | `Frio` (default) or `Quente/Frio` |\n"
        "| **Forma** | Omitted when Hi-Wall (default); shown for Janela, Cassete, Piso-Teto… |\n"
        "| **Cor** | Omitted when white (default); shown for Preto, etc. |\n"
        "| **Fallback** | Name unchanged when brand or BTU cannot be identified |\n"
    )
