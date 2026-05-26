"""Import History page — upload past CSV exports back to Supabase."""

from pathlib import Path

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def page_import_history():
    st.title("📂 Import History")
    st.caption("Upload historical CSV files to Supabase. Duplicates are ignored automatically.")

    OUTPUT_DIR = PROJECT_ROOT / "output"

    tab_folder, tab_upload = st.tabs(["From output/ folder", "Upload files"])

    # --- Tab 1: scan output/ folder ---
    with tab_folder:
        st.markdown("Scans the `output/` folder and imports all `rac_monitoramento_*.csv` files.")

        if not OUTPUT_DIR.exists():
            st.warning(f"Folder `output/` not found at `{OUTPUT_DIR}`.")
        else:
            csv_files = sorted(OUTPUT_DIR.glob("rac_monitoramento_*.csv"), reverse=True)

            if not csv_files:
                st.info("No CSV files found in `output/`.")
            else:
                # Preview table
                preview = []
                for f in csv_files:
                    try:
                        df_preview = pd.read_csv(f, sep=";", encoding="utf-8-sig", nrows=1)
                        rows = sum(1 for _ in open(f, encoding="utf-8-sig")) - 1
                    except Exception:
                        rows = "?"
                    preview.append({"File": f.name, "Rows": rows})

                st.dataframe(pd.DataFrame(preview), use_container_width=True, hide_index=True)
                st.caption(f"Total: {len(csv_files)} files")

                if st.button("⬆️ Import all to Supabase", type="primary"):
                    from utils.supabase_client import upload_to_supabase
                    progress = st.progress(0, text="Starting...")
                    log_area = st.empty()
                    log_lines = []
                    total_ok = 0

                    for i, f in enumerate(csv_files):
                        progress.progress((i + 1) / len(csv_files), text=f"Importing {f.name}...")
                        try:
                            df_csv = pd.read_csv(f, sep=";", encoding="utf-8-sig", dtype=str)
                            df_csv = df_csv.dropna(how="all")
                            records = df_csv.where(pd.notna(df_csv), None).to_dict("records")
                            ok = upload_to_supabase(records)
                            status = "✓" if ok else "✗"
                            total_ok += len(records) if ok else 0
                        except Exception as e:
                            status = f"✗ {e}"
                        log_lines.append(f"{status}  {f.name}  ({len(records)} rows)")
                        log_area.code("\n".join(log_lines), language="bash")

                    progress.empty()
                    st.success(f"Done. {total_ok:,} records sent. Duplicates were ignored.")

    # --- Tab 2: upload files ---
    with tab_upload:
        st.markdown("Upload one or more CSV files directly from your computer.")
        uploaded = st.file_uploader(
            "Select CSV files",
            type=["csv"],
            accept_multiple_files=True,
            help="Must be rac_monitoramento_*.csv format (semicolon-separated, UTF-8 BOM)",
        )

        if uploaded:
            total_rows = 0
            all_records = []
            for f in uploaded:
                try:
                    df_up = pd.read_csv(f, sep=";", encoding="utf-8-sig", dtype=str)
                    df_up = df_up.dropna(how="all")
                    records = df_up.where(pd.notna(df_up), None).to_dict("records")
                    all_records.extend(records)
                    total_rows += len(records)
                    st.write(f"✓ `{f.name}` — {len(records)} rows")
                except Exception as e:
                    st.error(f"✗ `{f.name}`: {e}")

            if all_records:
                st.info(f"Ready to import **{total_rows:,}** records total.")
                if st.button("⬆️ Send to Supabase", type="primary"):
                    from utils.supabase_client import upload_to_supabase
                    with st.spinner(f"Uploading {total_rows:,} records..."):
                        ok = upload_to_supabase(all_records)
                    if ok:
                        st.success(f"✅ {total_rows:,} records imported. Duplicates ignored.")
                    else:
                        st.error("Upload failed. Check Supabase connection.")
