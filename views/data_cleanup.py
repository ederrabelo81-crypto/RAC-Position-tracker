"""Data Cleanup page — scan & delete non-AC records and bad prices in Supabase."""

import streamlit as st


def page_data_cleanup():
    st.title("🧹 Data Cleanup")
    st.caption(
        "Scans Supabase for records that are not air-conditioner products "
        "(e.g. iPhones, diapers, notebooks) and removes them."
    )

    st.info(
        "**How it works:** Each record's product name is checked against a list of "
        "strong AC terms (BTU, ar condicionado, evaporadora…), weak terms (split, inverter), "
        "and a blocklist of known non-AC products. Records that fail the check are flagged for deletion."
    )

    col1, col2 = st.columns(2)

    # --- Scan ---
    with col1:
        scan_btn = st.button("🔍 Scan for invalid records", use_container_width=True)

    with col2:
        delete_btn = st.button(
            "🗑️ Delete invalid records",
            type="primary",
            use_container_width=True,
            help="Permanently removes all records that don't pass the AC product filter.",
        )

    if scan_btn:
        with st.spinner("Scanning Supabase… this may take a moment for large datasets."):
            from utils.supabase_client import delete_invalid_from_supabase
            result = delete_invalid_from_supabase(dry_run=True)

        st.session_state["cleanup_scan"] = result

    if "cleanup_scan" in st.session_state:
        r = st.session_state["cleanup_scan"]
        c1, c2, c3 = st.columns(3)
        c1.metric("Records scanned", f"{r['scanned']:,}")
        c2.metric("Invalid (non-AC)", f"{r['invalid']:,}", delta=f"-{r['invalid']:,}" if r["invalid"] else None, delta_color="inverse")
        c3.metric("Valid", f"{r['scanned'] - r['invalid']:,}")

        if r["invalid"] == 0:
            st.success("✅ No invalid records found. Your dataset is clean!")
        else:
            pct = r["invalid"] / r["scanned"] * 100 if r["scanned"] else 0
            st.warning(
                f"Found **{r['invalid']:,}** records ({pct:.1f}%) that appear unrelated "
                f"to air-conditioners. Click **Delete invalid records** to remove them."
            )

    if delete_btn:
        if "cleanup_scan" not in st.session_state or st.session_state["cleanup_scan"]["invalid"] == 0:
            st.warning("Run a scan first to confirm there are invalid records.")
        else:
            with st.spinner("Deleting invalid records…"):
                from utils.supabase_client import delete_invalid_from_supabase
                result = delete_invalid_from_supabase(dry_run=False)

            if result["errors"] == 0:
                st.success(
                    f"✅ Done. **{result['deleted']:,}** invalid records deleted. "
                    f"Your dataset now contains only AC-related products."
                )
            else:
                st.warning(
                    f"Partial cleanup: {result['deleted']:,} deleted, "
                    f"{result['errors']:,} with errors. Check Supabase logs."
                )
            # Clear cached scan result
            del st.session_state["cleanup_scan"]

    st.divider()
    # ── Price Validation ─────────────────────────────────────────────────────
    st.subheader("💰 Price Validation")
    st.caption(
        "Identifies records where the price significantly exceeds the reasonable ceiling "
        "for the detected BTU capacity — likely caused by historical parsing errors (×10 bug). "
        "E.g., a 9.000 BTU AC priced at R$ 18.990 instead of ~R$ 1.899."
    )

    col_p1, col_p2 = st.columns(2)
    with col_p1:
        price_scan_btn = st.button(
            "🔍 Scan for bad prices",
            use_container_width=True,
            key="price_scan_btn",
        )
    with col_p2:
        price_delete_btn = st.button(
            "🗑️ Delete records with bad prices",
            type="primary",
            use_container_width=True,
            key="price_delete_btn",
            help="Permanently removes records where price exceeds the BTU-based ceiling.",
        )

    if price_scan_btn:
        with st.spinner("Scanning for suspicious prices… this may take a moment."):
            from utils.supabase_client import scan_fix_bad_prices_in_supabase
            price_result = scan_fix_bad_prices_in_supabase(dry_run=True)
        st.session_state["price_scan"] = price_result

    if "price_scan" in st.session_state:
        pr = st.session_state["price_scan"]
        pc1, pc2 = st.columns(2)
        pc1.metric("Records scanned", f"{pr['scanned']:,}")
        pc2.metric(
            "Suspicious prices",
            f"{pr['suspicious']:,}",
            delta=f"-{pr['suspicious']:,}" if pr["suspicious"] else None,
            delta_color="inverse",
        )

        if pr["suspicious"] == 0:
            st.success("✅ No price anomalies found!")
        else:
            pct = pr["suspicious"] / pr["scanned"] * 100 if pr["scanned"] else 0
            st.warning(
                f"Found **{pr['suspicious']:,}** records ({pct:.1f}%) with suspiciously high "
                "prices. These are likely ×10 parsing errors. "
                "Click **Delete records with bad prices** to remove them."
            )
            if pr.get("examples"):
                with st.expander(f"Examples ({len(pr['examples'])} shown)", expanded=True):
                    st.dataframe(pr["examples"], use_container_width=True, hide_index=True)

    if price_delete_btn:
        scan = st.session_state.get("price_scan")
        if not scan or scan["suspicious"] == 0:
            st.warning("Run a price scan first to confirm there are records to remove.")
        else:
            with st.spinner(f"Deleting {scan['suspicious']:,} records with bad prices…"):
                from utils.supabase_client import scan_fix_bad_prices_in_supabase
                price_result = scan_fix_bad_prices_in_supabase(dry_run=False)
            if price_result["errors"] == 0:
                st.success(
                    f"✅ Done. **{price_result['deleted']:,}** records with bad prices deleted."
                )
            else:
                st.warning(
                    f"Partial cleanup: {price_result['deleted']:,} deleted, "
                    f"{price_result['errors']:,} with errors. Check Supabase logs."
                )
            if "price_scan" in st.session_state:
                del st.session_state["price_scan"]

    st.divider()
    st.markdown(
        "**Price ceilings by BTU capacity** *(prices above these are flagged)*\n\n"
        "| Capacity | Max reasonable |\n|---|---|\n"
        "| 7.000 BTUs | R$ 4.500 |\n"
        "| 9.000 BTUs | R$ 5.500 |\n"
        "| 12.000 BTUs | R$ 7.000 |\n"
        "| 18.000 BTUs | R$ 12.000 |\n"
        "| 24.000 BTUs | R$ 16.000 |\n"
        "| 36.000 BTUs | R$ 28.000 |\n"
        "| 48.000 BTUs | R$ 40.000 |\n"
        "| 60.000 BTUs | R$ 55.000 |\n"
    )

    st.divider()
    st.subheader("Filter rules reference")

    col_a, col_b, col_c = st.columns(3)
    with col_a:
        st.markdown("**✅ Strong AC terms** *(any one = keep)*")
        st.code(
            "ar condicionado\nBTU / BTUs\nevaporadora\ncondensadora\nhi-wall\nmini-split\ncassete",
            language=None,
        )
    with col_b:
        st.markdown("**🟡 Weak AC terms** *(need 2+ = keep)*")
        st.code("split\ninverter", language=None)
    with col_c:
        st.markdown("**🚫 Blocklist** *(any one = remove)*")
        st.code(
            "iphone / ipad\nnotebook / laptop\ncelular / smartphone\nfralda\ngeladeira / refrigerador\nfogão / microondas\ntablet / airpods / macbook\ncolchão / sofá",
            language=None,
        )
