#SSELF_hierarchy_app.py
import os, sys, io, random
import numpy as np
import pandas as pd
import streamlit as st
from contextlib import redirect_stdout

# ===== Locate your code package (…\2nd paper\SSELF_python_code) =====
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # up to "2nd paper"
CODE_DIR = os.path.join(BASE_DIR, "SSELF_python_code")
if CODE_DIR not in sys.path:
    sys.path.insert(0, CODE_DIR)

from SSELF_base import FootprintDatabase
from hierarchy_extension import HierarchicalSystem  # uses your fixed code

st.set_page_config(page_title="SSELF — Hierarchy Extension", layout="wide")
st.title("SSELF — Hierarchy Extension (CSV-driven)")

# --------------------------- Sidebar ---------------------------
with st.sidebar:
    st.header("Inputs")
    st.caption("Upload the **Products** and **Companies** CSVs used by your notebook:")
    up_products = st.file_uploader("Products CSV", type=["csv"], key="products_csv")
    up_companies = st.file_uploader("Companies CSV", type=["csv"], key="companies_csv")

    st.divider()
    st.caption("…or provide absolute file paths (optional, if not uploading):")
    products_path = st.text_input("Products path", value="", placeholder=r"C:\path\to\products_to_read.csv")
    companies_path = st.text_input("Companies path", value="", placeholder=r"C:\path\to\companies_to_read.csv")

    st.divider()
    st.header("Run settings")
    verbose = st.checkbox("Verbose logs", value=True)
    random_state = st.number_input("Random state (optional)", value=0, step=1)
    random_state = int(random_state) if random_state != 0 else None
    max_iter = st.number_input("Max iterations", min_value=100, max_value=20000, value=5000, step=100)

    run_btn = st.button("Run hierarchy simulation", type="primary")
    clear_btn = st.button("Clear results")

if clear_btn:
    st.session_state.pop("hier_sim", None)
    st.rerun()

# --------------------------- Helpers ---------------------------
def _read_csvs(up_products, up_companies, products_path, companies_path):
    """Return (df_products, df_companies) as DataFrames; prefers uploads over paths."""
    dfp, dfc = None, None
    # Products
    if up_products is not None:
        dfp = pd.read_csv(up_products)
    elif products_path.strip():
        dfp = pd.read_csv(products_path.strip(), dtype=str)
    # Companies
    if up_companies is not None:
        dfc = pd.read_csv(up_companies)
    elif companies_path.strip():
        dfc = pd.read_csv(companies_path.strip(), dtype=str)
    return dfp, dfc

def _print_tree(company, indent=0):
    line = "    " * indent + f"- {company.name} (latest_update={company.latest_update})"
    yield line
    for sub in getattr(company, "sub_companies", []):
        yield from _print_tree(sub, indent + 1)

def run_hierarchy_sim(dfp: pd.DataFrame, dfc: pd.DataFrame, verbose=True, random_state=None, max_iter=5000):
    # 1) Build system from provided frames (mirrors your notebook)
    system = HierarchicalSystem(num_companies=0, num_products=0)
    system._build_from_frames(dfc.fillna(""), dfp.fillna(""))

    # 2) Create DB and seed placeholder scores for all internal product IDs
    fp_db = FootprintDatabase(year=2024)
    for pid in system._product_id_to_code.keys():  # internal integer IDs
        fp_db.report(int(pid), 0.0)

    # ---- NEW: per-iteration history capture (best-effort) ----
    history_rows = []
    def hook(iter_idx, sys_obj):
        for ent in sys_obj._iter_all_entities():
            for p in getattr(ent, "products", []):
                history_rows.append({
                    "iteration": iter_idx,
                    "entity": ent.name,
                    "product_id": p.product_id,
                    "product_name": getattr(p, "name", f"Product {p.product_id}"),
                    "footprint": float(getattr(p, "footprint", float("nan"))),
                    "update_count": int(getattr(ent, "num_updates", 0)),
                })

    # 3) Capture logs from solve()
    f = io.StringIO()
    with redirect_stdout(f):
        try:
            # Prefer using a progress callback if your solver supports it
            system.solve(
                fp_db,
                verbose=verbose,
                random_state=random_state,
                max_iter=max_iter,
                progress_callback=hook,   # <--- may not exist; handled below
            )
        except TypeError:
            # Fallback if progress_callback isn't in the signature
            system.solve(fp_db, verbose=verbose, random_state=random_state, max_iter=max_iter)
    logs = f.getvalue()

    # Build history DataFrame (may be empty if no callback)
    df_products_hist = pd.DataFrame(history_rows)

    # 4) Final company table (top-level companies only; tree below shows subs)
    rows = []
    for cname, company in system.companies.items():
        rows.append({
            "company_key": cname,
            "display_name": company.name,
            "latest_update": company.latest_update
        })
    df_companies_final = pd.DataFrame(rows).sort_values("company_key")

    # 5) Build hierarchy tree string for display
    tree_lines = []
    for cname, root in system.companies.items():
        tree_lines.extend(list(_print_tree(root)))
    tree_txt = "\n".join(tree_lines) if tree_lines else "(No hierarchy found.)"

    # 6) Carbon balance
    df_balance = system.carbon_balance_all(fp_db, atol=1e-6, verbose=False)
    balance_summary = system.carbon_balance_summary(fp_db, atol=1e-6)

    return {
        "system": system,
        "footprint_db": fp_db,
        "df_companies_final": df_companies_final,
        "tree_txt": tree_txt,
        "logs": logs,
        "df_balance": df_balance,
        "balance_summary": balance_summary,
        "df_products_hist": df_products_hist,   # <--- NEW
    }

import html

def hierarchy_to_graphviz(system, fp_db, rankdir="LR"):
    """
    Build a DOT graph of the hierarchy.
    - Companies: rounded boxes, colored by carbon balance (green/red).
    - Products: ellipses connected to their company.
    """
    # Compute balance once for coloring
    bal_df = system.carbon_balance_all(fp_db, atol=1e-6, verbose=False).set_index("entity") if fp_db else None

    def bal_status(ent_name):
        if bal_df is None or ent_name not in bal_df.index:
            return None
        return bool(bal_df.loc[ent_name, "balanced"])

    def esc(s):  # escape labels
        return html.escape(str(s), quote=True)

    lines = [
        f'digraph G {{',
        f'  rankdir={rankdir};',
        '  fontsize=10;',
        '  node [fontname="Helvetica"];',
        '  edge [color="#888888"];'
    ]

    # Traverse the hierarchy
    for root in system.companies.values():
        stack = [root]
        while stack:
            ent = stack.pop()

            ok = bal_status(ent.name)
            fill = "#E6F4EA" if ok is True else ("#FDECEA" if ok is False else "#F3F4F6")
            border = "#2E7D32" if ok is True else ("#C62828" if ok is False else "#6B7280")
            tooltip = f"{ent.name}\\nlatest_update={getattr(ent, 'latest_update', 'NA')}"

            # Company node
            lines.append(
                f'  "{esc(ent.name)}" [shape=box, style="filled,rounded", color="{border}", '
                f'fillcolor="{fill}", penwidth=1.4, tooltip="{esc(tooltip)}"];'
            )

            # Edges to sub-companies
            for sub in getattr(ent, "sub_companies", []):
                lines.append(f'  "{esc(ent.name)}" -> "{esc(sub.name)}" [arrowhead=vee, arrowsize=0.8];')
                stack.append(sub)

            # Product leaves
            sales_df = ent.sales if isinstance(ent.sales, pd.DataFrame) else pd.DataFrame()
            for p in getattr(ent, "products", []):
                label = getattr(p, "name", f"Product {p.product_id}")
                score = float(getattr(p, "footprint", 0.0))
                sales = (
                    float(sales_df.loc[p.product_id, "Sales"])
                    if (not sales_df.empty and "Sales" in sales_df.columns and p.product_id in sales_df.index)
                    else 0.0
                )
                ptip = f"{label}\\nID={p.product_id}\\nscore={score:.4f} kg CO2e/$\\nsales={sales:.4f}"
                pfill = "#E3F2FD" if ok is True else ("#FFF3E0" if ok is False else "#F3F4F6")

                lines.append(
                    f'  "p_{p.product_id}" [label="{esc(label)}", shape=ellipse, style=filled, '
                    f'fillcolor="{pfill}", color="#1565C0", tooltip="{esc(ptip)}"];'
                )
                lines.append(f'  "{esc(ent.name)}" -> "p_{p.product_id}" [style=dashed, color="#9E9E9E"];')

    lines.append('}')
    return "\n".join(lines)

def render_results(res, verbose=True):
    # Define the tabs for all UI
    tabs = st.tabs(["Overview", "Hierarchy tree", "Proof check: Impact balance", "Footprint evolution", "Logs"])


    # ---- Overview ----
    with tabs[0]:
        system = res["system"]

        st.subheader("Product carbon footprint results")
        # Build final product table from the system (entity-by-entity)
        prod_rows = []
        for ent in system._iter_all_entities():
            for p in getattr(ent, "products", []):
                prod_rows.append({
                    "Entity": ent.name,
                    "Product ID": p.product_id,
                    "Product name": getattr(p, "name", f"Product {p.product_id}"),
                    "Footprint score": float(getattr(p, "footprint", 0.0)),
                    "Unit": "kg CO2e/$",
                    "Update count": int(getattr(ent, "num_updates", 0)),
                })

        df_products_final = pd.DataFrame(prod_rows)
        if not df_products_final.empty:
            df_products_final = (
                df_products_final
                .sort_values(["Entity", "Product ID"])
                .reset_index(drop=True)
            )
            st.dataframe(df_products_final, use_container_width=True)
        else:
            st.info("No product results.")

        st.markdown("---")
        st.subheader("Final company results (top-level, split by scope)")

        # Helper: iterate subtree for a top-level company
        def iter_subtree(root):
            yield root
            for s in getattr(root, "sub_companies", []):
                yield from iter_subtree(s)

        comp_rows = []
        for _, top in system.companies.items():
            scope1 = 0.0
            total_out = 0.0
            for ent in iter_subtree(top):
                # Scope 1: direct impacts
                if getattr(ent, "direct_impacts", None) is not None and not ent.direct_impacts.empty:
                    try:
                        scope1 += float(ent.direct_impacts.sum(numeric_only=True).sum())
                    except Exception:
                        pass
                # Outputs = sum(footprint * sales)
                sales_df = ent.sales if isinstance(ent.sales, pd.DataFrame) else pd.DataFrame()
                for p in getattr(ent, "products", []):
                    if not sales_df.empty and "Sales" in sales_df.columns and p.product_id in sales_df.index:
                        try:
                            s_val = float(sales_df.loc[p.product_id, "Sales"])
                        except Exception:
                            s_val = 0.0
                        total_out += float(getattr(p, "footprint", 0.0)) * s_val

            scope23 = max(total_out - scope1, 0.0)
            comp_rows.append({
                "Company name": top.name,
                "Scope 1 (direct) [kg CO2e]": scope1,
                "Scope 2–3 (indirect) [kg CO2e]": scope23,
            })

        df_companies_scopes = pd.DataFrame(comp_rows)
        if not df_companies_scopes.empty:
            df_companies_scopes = df_companies_scopes.sort_values("Company name").reset_index(drop=True)
            st.dataframe(df_companies_scopes, use_container_width=True)
        else:
            st.info("No top-level company results.")

    # ---- Hierarchy tree ----
    with tabs[1]:
        st.subheader("Hierarchy (final state)")

        system = res.get("system", None)
        fp_db = res.get("footprint_db", None)

        if system is None or not getattr(system, "companies", {}):
            st.info("No hierarchy found.")
        else:
            # Build DOT and render
            dot = hierarchy_to_graphviz(system, fp_db, rankdir="LR")  # use "TB" for top→bottom if you prefer
            st.graphviz_chart(dot, use_container_width=True)
            st.caption(
                "Legend: green = balanced, red = not balanced. Products are ellipses; companies are rounded boxes.")

    # ---- Carbon balance ----
    with tabs[2]:
        st.subheader("Proof check: Per-entity impact balance (CO2 in = CO2 out)")

        system = res.get("system", None)
        fp_db = res.get("footprint_db", None)

        if system is None or fp_db is None:
            st.warning("Missing system or footprint database in results.")
        else:
            try:
                dfb = system.carbon_balance_all(fp_db, atol=1e-6, verbose=False).copy()

                if dfb.empty:
                    st.info("No balance rows produced. (Check that entities/products exist and solver completed.)")
                else:
                    # Ensure numeric types for pretty display
                    numeric_cols = ["in_emb", "in_direct", "in_total", "out_total", "delta"]
                    for c in numeric_cols:
                        if c in dfb.columns:
                            dfb[c] = pd.to_numeric(dfb[c], errors="coerce")

                    # Optional formatting (comment out if you want raw floats)
                    dfb_display = dfb.copy()
                    for c in numeric_cols:
                        if c in dfb_display.columns:
                            dfb_display[c] = dfb_display[c].map(lambda x: f"{x:.6f}" if pd.notnull(x) else "")

                    dfb_display = dfb_display.reset_index(drop=True)
                    st.dataframe(dfb_display, use_container_width=True)

                    # Summary
                    summ = system.carbon_balance_summary(fp_db, atol=1e-6)
                    st.markdown(
                        f"**Summary**  \n"
                        f"- Sum(in_total): `{summ['sum_in_total']:.6f}`  \n"
                        f"- Sum(out_total): `{summ['sum_out_total']:.6f}`  \n"
                        f"- Sum(delta): `{summ['sum_delta']:.6f}`  \n"
                        f"- All entities balanced: `{summ['all_balanced']}`"
                    )

                    if "balanced" in dfb.columns:
                        bad = dfb[~dfb["balanced"]]
                        if not bad.empty:
                            st.warning(
                                f"{len(bad)} entities are not balanced within tolerance. "
                                f"Check purchases indexing, missing scores, sales recomputation, or self-consumption masks."
                            )
            except Exception as e:
                st.error("Carbon balance computation failed.")
                st.exception(e)


    # ---- Footprint evolution ----
    with tabs[3]:
        st.subheader("Footprint evolution by product (kg CO2e/$)")

        df_hist = res.get("df_products_hist", pd.DataFrame())
        if df_hist is None or df_hist.empty:
            st.info(
                "No evolution history captured. "
                "This can happen if the solver doesn't support a progress callback. "
                "If desired, add a `progress_callback(iter_idx, system)` parameter to HierarchicalSystem.solve(...) "
                "and call it once per iteration."
            )
        else:
            product_list = (
                df_hist[["product_id", "product_name"]]
                .drop_duplicates()
                .sort_values("product_id")
            )

            default_choices = st.session_state.get(
                "hier_selected_products",
                product_list["product_id"].head(8).tolist()
            )
            choices = st.multiselect(
                "Choose products to plot",
                product_list["product_id"].tolist(),
                default=default_choices,
                key="hier_selected_products",
            )

            plot_df = (
                df_hist[df_hist["product_id"].isin(choices)]
                .pivot_table(index="iteration", columns="product_id", values="footprint")
                .sort_index()
            )
            if plot_df.empty:
                st.info("Select at least one product with recorded history.")
            else:
                st.line_chart(plot_df, use_container_width=True)

            with st.expander("Show evolution table"):
                st.dataframe(
                    df_hist[df_hist["product_id"].isin(choices)]
                    .sort_values(["product_id", "iteration"])
                    .reset_index(drop=True),
                    use_container_width=True
                )

    # ---- Logs ----
    with tabs[3]:
        st.subheader("Logs")
        if verbose:
            st.code(res.get("logs", ""), language="text")
        else:
            st.caption("Verbose logging is off. Enable it in the sidebar to see solver prints.")


# --------------------------- Run / UI flow ---------------------------
if run_btn:
    dfp, dfc = _read_csvs(up_products, up_companies, products_path, companies_path)
    if dfp is None or dfc is None:
        st.error("Please provide both CSVs (upload or paths).")
    else:
        with st.spinner("Building system and running solver…"):
            res = run_hierarchy_sim(dfp, dfc, verbose=verbose, random_state=random_state, max_iter=max_iter)
        st.session_state["hier_sim"] = res
        st.success("Done.")
        render_results(res, verbose=verbose)

elif "hier_sim" in st.session_state:
    render_results(st.session_state["hier_sim"], verbose=verbose)

else:
    st.info("Upload **Products** and **Companies** CSVs (or provide paths), set options, then click **Run hierarchy simulation**.")
