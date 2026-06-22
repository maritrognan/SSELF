import os, sys, io, random
import numpy as np
import pandas as pd
import streamlit as st
from contextlib import redirect_stdout

# ---- code path setup ----
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODE_DIR = os.path.join(BASE_DIR, "SSELF_python_code")
if CODE_DIR not in sys.path:
    sys.path.insert(0, CODE_DIR)

from substitution_extension import (
    ClassificationDatabase, SalesDatabase,
    Product, Company, System as SubstSystem,
)
from SSELF_base import FootprintDatabase

def _load_demo_classification_data():
    return [
        {
            "class_code": "HS 2517",
            "class_name": "Crushed stone, gravel and similar mineral aggregates",
            "function": "Provide compactable mineral aggregate for construction fill or sub-base",
            "unit": "m3 compacted aggregate-equivalent",
        },
        {
            "class_code": "HS 2516",
            "class_name": "Granite, basalt, sandstone and other building stone",
            "function": "Provide compactable mineral aggregate for construction fill or sub-base",
            "unit": "m3 compacted aggregate-equivalent",
        },
        {
            "class_code": "HS 2523",
            "class_name": "Portland cement and other hydraulic cements",
            "function": "Provide hydraulic binder for cementitious mixtures",
            "unit": "kg hydraulic binder-equivalent",
        },
        {
            "class_code": "HS 2618",
            "class_name": "Granulated slag from iron or steel manufacture",
            "function": "Provide hydraulic binder for cementitious mixtures",
            "unit": "kg hydraulic binder-equivalent",
        },
        {
            "class_code": "HS 2621",
            "class_name": "Ash and residues from combustion processes",
            "function": "Provide supplementary cementitious material for cementitious mixtures",
            "unit": "kg binder-equivalent",
        },
        {
            "class_code": "HS 7204",
            "class_name": "Ferrous waste and scrap",
            "function": "Provide ferrous metallic feedstock for steelmaking",
            "unit": "kg Fe-equivalent",
        },
        {
            "class_code": "HS 7207",
            "class_name": "Semi-finished products of iron or non-alloy steel",
            "function": "Provide ferrous metallic feedstock for steelmaking",
            "unit": "kg Fe-equivalent",
        },
        {
            "class_code": "HS 3901",
            "class_name": "Polyethylene",
            "function": "Provide thermoplastic polymer feedstock for polyethylene applications",
            "unit": "kg polymer-equivalent",
        },
        {
            "class_code": "HS 3915",
            "class_name": "Waste, parings and scrap of plastics",
            "function": "Provide thermoplastic polymer feedstock for polyethylene applications",
            "unit": "kg polymer-equivalent",
        },
        {
            "class_code": "HS 2710",
            "class_name": "Petroleum oils and oils from bituminous minerals",
            "function": "Provide liquid fuel energy for combustion applications",
            "unit": "MJ lower-heating-value",
        },
        {
            "class_code": "HS 3826",
            "class_name": "Biodiesel and mixtures thereof",
            "function": "Provide liquid fuel energy for combustion applications",
            "unit": "MJ lower-heating-value",
        },
        {
            "class_code": "HS 2905",
            "class_name": "Acyclic alcohols and derivatives",
            "function": "Provide alcohol-based chemical feedstock or solvent function",
            "unit": "kg alcohol-equivalent",
        },
        {
            "class_code": "HS 2207",
            "class_name": "Ethyl alcohol, undenatured or denatured",
            "function": "Provide alcohol-based chemical feedstock or solvent function",
            "unit": "kg alcohol-equivalent",
        },
        {
            "class_code": "HS 8507",
            "class_name": "Electric accumulators",
            "function": "Provide rechargeable electrical energy storage capacity",
            "unit": "kWh storage-capacity-equivalent",
        },
    ]

st.set_page_config(page_title="SSELF — System Expansion & Substitution", layout="wide")
st.title("SSELF — System Expansion & Substitution")
st.markdown("""
**What this page demonstrates**

- **Purpose:** Handle multi-functionality via *system expansion & substitution*.
- **What you (a company) provide:**  
  1) Your **secondary co-product** (name, class code, unit),  
  2) Your **function output factor** (how much “function” per unit of your co-product),  
  3) Your **own last-year sales** of that co-product (units).
- **What already exists (platform / third-party):**  
  - A **Classification database** (e.g., HS codes) → used to understand the *function* and *units* tied to a class code.  
  - A **Last-year market sales database** → used to compute *market-average* intensities for substitution.  
    This dataset is **sensitive** and is **not browsable** here.

**How substitution works (in this demo)**  
We compute your company’s total impacts (purchases @ current scores + direct).  
Each *primary* product starts with that total. For each *secondary* product, we compute a credit using the **market-average intensity** (from last year’s data) × **your secondary sales** × **function output factor**, subtract that from the primary bucket(s), and give the secondary an intensity so that outputs sum to inputs (carbon balance).
""")
st.info("Tip: You can **consult the Classification DB** below to pick the right class code. The **Last-year sales DB** is referenced for market averages but isn’t shown here.")

# ---------------- Sidebar ----------------
with st.sidebar:
    with st.sidebar:
        st.header("Data sources")

        st.markdown("""
    **Classification database (consultable)**
    - Pre-existing dataset managed by the platform or a third party.
    - Used to attach a **function** and **unit** to a class code.
    - You can browse it on the main page below.

    **Last-year market sales (referenced, sensitive)**
    - Pre-existing aggregated dataset.
    - Used only to compute **market-average intensities** for substitution.
    - Not browsable here; you may optionally supply your own copy for testing.
    """)

        # Optional advanced uploads for testing / demos
        with st.expander("Advanced: override data with your own CSVs (optional)"):
            up_class = st.file_uploader("Classification CSV", type=["csv"], key="class_csv")
            up_sales = st.file_uploader("Last-year Sales CSV", type=["csv"], key="sales_csv")

        st.divider()
        st.header("Run settings")
        size = st.number_input("System size (number of companies)", 3, 200, 13, step=1)
        seed = st.number_input("Random seed (optional)", value=42, step=1)
        max_iter = st.number_input("Max iterations", 50, 5000, 200, step=50)
        verbose = st.checkbox("Verbose logs", value=True)

        st.divider()
        st.header("Your co-product (company input)")
        add_demo = st.checkbox("Add demo secondary outputs", value=True,
                               help="Adds one co-product to the selected company to demonstrate substitution.")
        demo_company_idx = st.number_input("Producing company", 1, int(size), 11, step=1)
        sec_name = st.text_input("Co-product name", "examples: Granulated slag / plastic scrap / recovered alcohol")
        sec_class = st.text_input("Co-product classification code", "HS 2618",
                                help = "The classification code must correspond to an existing code in the demo classification database.")


    # --- Resolve function and reference unit from classification code ---
    _unit_hint = None
    _func_hint = None

    try:
        if up_class is not None:
            _cls_df = pd.read_csv(up_class)
        else:
            _cls_df = pd.DataFrame(_load_demo_classification_data())

        _cls_df["class_code"] = _cls_df["class_code"].astype(str).str.strip()
        sec_class_clean = str(sec_class).strip()

        row = _cls_df[_cls_df["class_code"] == sec_class_clean]

        if not row.empty:
            _func_hint = str(row.iloc[0]["function"])
            _unit_hint = str(row.iloc[0]["unit"])

    except Exception as e:
        st.error(f"Could not resolve classification code: {e}")

    if _func_hint and _unit_hint:
        st.caption(f"Function for **{sec_class}**: {_func_hint}")
        st.caption(f"Reference unit of substituted function: `{_unit_hint}`")
        sec_unit = _unit_hint
    else:
        st.warning(
            "The selected co-product classification code was not found in the classification database. Choose a valid class code before running the simulation."
        )
        sec_unit = None

    sec_function_output = st.number_input(
        f"Function-output factor [{_unit_hint or 'reference unit'} per $ of co-product sales]",
        min_value=0.0,
        value=0.05,
        step=0.01,
        help=(
            "Quantity of substituted function delivered per dollar of co-product sales. The reference unit is resolved from the co-product classification code."
        )
    )
    sec_sales = st.number_input(
        "Co-product sales value in the reporting period ($)",
        min_value=0.0, value=100.0, step=10.0,
        help="How much you co-product you sold during the reporting period in \$. The market averages and sales volumes come from the Last-year Sales table (upload or demo)."
    )


    st.divider()
    run_btn = st.button("Run substitution simulation", type="primary")
    clear_btn = st.button("Clear results")

if clear_btn:
    st.session_state.pop("subst_sim", None)
    st.rerun()

# ------------- helpers -------------
def _load_classification_db(up_file):
    if up_file is not None:
        df = pd.read_csv(up_file)
        req = {"class_code", "class_name", "function", "unit"}
        missing = req - set(df.columns)
        if missing:
            st.error(f"Classification CSV missing columns: {missing}")
            return None
        return ClassificationDatabase(df)

    return ClassificationDatabase(_load_demo_classification_data())




def _build_last_year_sales(year, class_db, up_file):
    sales_db = SalesDatabase(year, class_db)
    if up_file is not None:
        df = pd.read_csv(up_file)
        req = {"id","class_code","sales_volume","unit","function_output"}
        missing = req - set(df.columns)
        if missing:
            st.error(f"Sales CSV missing columns: {missing}")
            return None
        # type safety
        df = df.copy()
        df["id"] = pd.to_numeric(df["id"], errors="coerce").fillna(0).astype(int)
        df["sales_volume"] = pd.to_numeric(df["sales_volume"], errors="coerce").fillna(0.0)
        df["function_output"] = pd.to_numeric(df["function_output"], errors="coerce").fillna(0.0)
        sales_db.data = df[["id","class_code","sales_volume","unit","function_output"]].copy()
        return sales_db

    # demo fill (mirrors your notebook)
    # seed three “primary” references to ensure substitution has a pool
    # Demo last-year market sales database.
    # sales_volume is interpreted as annual sales value.
    # function_output is reference units of function per dollar of sales.
    demo_rows = []
    next_id = 1000

    default_function_output = {
        "Provide compactable mineral aggregate for construction fill or sub-base": 0.03,
        "Provide hydraulic binder for cementitious mixtures": 0.04,
        "Provide supplementary cementitious material for cementitious mixtures": 0.04,
        "Provide ferrous metallic feedstock for steelmaking": 0.08,
        "Provide thermoplastic polymer feedstock for polyethylene applications": 0.06,
        "Provide liquid fuel energy for combustion applications": 2.5,
        "Provide alcohol-based chemical feedstock or solvent function": 0.05,
        "Provide edible cereal dry matter for food applications": 0.10,
        "Provide edible vegetable oil for food or oleochemical applications": 0.07,
        "Provide rechargeable electrical energy storage capacity": 0.002,
    }

    for code in class_db.data["class_code"].unique():
        class_name, function, unit = class_db.get_class_info(code)
        function_output = default_function_output.get(function, 1.0)

        for _ in range(2):
            demo_rows.append({
                "id": next_id,
                "class_code": code,
                "sales_volume": float(np.random.uniform(500, 2000)),
                "unit": unit,
                "function_output": float(function_output),
            })
            next_id += 1

    sales_db.data = pd.DataFrame(demo_rows)
    return sales_db

# ---- Classification browser (consultable, read-only) ----
st.markdown("### Browse the Classification database")
_preview_class_db = _load_classification_db(up_class)
if _preview_class_db is not None:
    df_cls = _preview_class_db.data.copy()

    # Filters
    c1, c2, c3 = st.columns([1,1,1])
    with c1:
        q_code = st.text_input("Filter by class code", "", placeholder="e.g., HS 2905").strip()
    with c2:
        q_name = st.text_input("Filter by class name", "", placeholder="e.g., Alcohols").strip()
    with c3:
        q_func = st.text_input("Filter by function", "", placeholder="e.g., Binder").strip()

    # Apply filters (case-insensitive)
    if q_code:
        df_cls = df_cls[df_cls["class_code"].astype(str).str.contains(q_code, case=False, na=False)]
    if q_name:
        df_cls = df_cls[df_cls["class_name"].astype(str).str.contains(q_name, case=False, na=False)]
    if q_func:
        df_cls = df_cls[df_cls["function"].astype(str).str.contains(q_func, case=False, na=False)]

    st.dataframe(df_cls.reset_index(drop=True), use_container_width=True, height=280)

    # Download demo (only when using demo data)
    if up_class is None:
        csv_bytes = _preview_class_db.data.to_csv(index=False).encode("utf-8")
        st.download_button(
            "Download demo classification CSV",
            data=csv_bytes,
            file_name="classification_demo.csv",
            mime="text/csv",
        )
else:
    st.warning("No classification data available.")

def _make_progress_hook(system):
    if not hasattr(system, "_ui_history"):
        system._ui_history = []
    def hook(iter_idx, sys_obj):
        # record product footprints per iteration
        for cname, comp in sys_obj.companies.items():
            for p in comp.products:
                sys_obj._ui_history.append({
                    "iteration": int(iter_idx),
                    "company": cname,
                    "product_id": int(p.product_id),
                    "product_name": getattr(p, "name", f"Product {p.product_id}"),
                    "footprint": float(getattr(p, "footprint", 0.0)),
                    "latest_update": float(getattr(comp, "latest_update", 0.0)) if comp.latest_update is not None else 0.0,
                })
    return hook

# ------------- runner -------------
def run_simulation(
    size:int, seed:int, max_iter:int, verbose:bool,
    classification_db: ClassificationDatabase,
    add_demo_secondary: bool, demo_company_idx:int, sec_name:str, sec_class:str, sec_unit:str,
    sec_function_output: float, sec_sales: float,
    last_year_sales: SalesDatabase,
):

    # reproducibility
    if seed is not None:
        random.seed(int(seed))
        np.random.seed(int(seed))

    # 1) build system
    system = SubstSystem(num_companies=int(size), num_products=int(size), classification_db=classification_db)

    # 2) optional: add one secondary co-product to a specific company (UI demo)
    if add_demo_secondary:
        key = f"Company_{int(demo_company_idx)}"
        if key in system.companies:
            comp = system.companies[key]
            new_id = int(max([p.product_id for p in comp.products] + [size]) + 1)
            sec = Product(new_id, sec_name, sec_unit, comp, sec_class, "secondary", float(sec_function_output), classification_db)
            comp.add_product(sec)
            if comp.sales.empty:
                comp.sales = pd.DataFrame(columns=["Sales"])
            comp.sales.loc[new_id] = [float(sec_sales)]

    # 3) initialize DBs
    fp_2023 = FootprintDatabase(2023)
    fp_2024 = FootprintDatabase(2024)

    for company in system.companies.values():
        for product in company.products:
            fp_2023.report(int(product.product_id), float(np.random.uniform(1, 100)))  # historical intensity
            fp_2024.report(int(product.product_id), 0.0)

    # also ensure all ids that appear in last_year_sales exist in 2023 DB
    for row in last_year_sales.data.itertuples():
        if int(row.id) not in fp_2023.data["id"].astype(int).values:
            fp_2023.report(int(row.id), float(np.random.uniform(1, 100)))

    # 4) run solver (captures iteration history)
    hook = _make_progress_hook(system)
    f = io.StringIO()
    with redirect_stdout(f):
        system.solve(
            fp_2024=fp_2024,
            fp_2023=fp_2023,
            last_year_sales=last_year_sales,
            atol=1e-6,
            max_iter=int(max_iter),
            seed=int(seed),
            verbose=verbose,
            progress_callback=hook,  # ← add this
        )
    logs = f.getvalue()

    # 5) build history tables
    df_hist = pd.DataFrame(getattr(system, "_ui_history", []))
    df_companies = (
        df_hist.groupby(["company", "iteration"], as_index=False)
               .agg(latest_update=("latest_update","last"))
        if not df_hist.empty else pd.DataFrame(columns=["company","iteration","latest_update"])
    )
    df_products = (
        df_hist.groupby(["product_id","product_name","iteration"], as_index=False)
               .agg(footprint=("footprint","last"))
        if not df_hist.empty else pd.DataFrame(columns=["product_id","product_name","iteration","footprint"])
    )

    return logs, df_companies, df_products, df_hist, system, fp_2024

# ------------- UI render -------------
def render_results(system, dfc, dfp, logs, verbose, fp_db):
    tabs = st.tabs(["Overview", "Footprint evolution", "Carbon balance", "Logs"])

    with tabs[0]:
        st.subheader("Product carbon footprint results")
        if not dfp.empty:
            # take the last recorded row per product (not the global last iteration)
            dfp_final = (
                dfp.sort_values(["product_id", "iteration"])
                .groupby(["product_id", "product_name"], as_index=False)
                .tail(1)
                .rename(columns={"footprint": "Footprint score"})
            )

            # Map product_id → company name
            pid2company = {}
            for cname, comp in system.companies.items():
                for p in comp.products:
                    pid2company[int(p.product_id)] = cname
            dfp_final["Company"] = dfp_final["product_id"].map(pid2company)

            # Attach company Update count (same as other versions)
            update_counts = {cname: int(getattr(comp, "num_updates", 0)) for cname, comp in system.companies.items()}
            dfp_final["Update count"] = dfp_final["Company"].map(update_counts)

            # Add unit column for consistency
            dfp_final["Unit"] = "kg CO2e/$"

            # Pretty order like the base app
            dfp_final = (
                dfp_final[["Update count", "product_id", "product_name", "Footprint score", "Unit", "Company"]]
                .rename(columns={"product_id": "Product ID", "product_name": "Product name"})
                .reset_index(drop=True)
            )
            st.dataframe(dfp_final, use_container_width=True)
        else:
            st.info("No product results.")

        st.markdown("---")
        st.subheader("Company GHG results (final, split by scope)")

        rows = []
        for cname, comp in system.companies.items():
            # Scope 1
            scope1 = 0.0
            if isinstance(comp.direct_impacts, pd.DataFrame) and not comp.direct_impacts.empty:
                try:
                    scope1 = float(comp.direct_impacts.sum(numeric_only=True).sum())
                except Exception:
                    scope1 = 0.0

            # Σ(footprint * sales) for own products
            out_total = 0.0
            sales_df = comp.sales if isinstance(comp.sales, pd.DataFrame) else pd.DataFrame()
            for p in getattr(comp, "products", []):
                s = 0.0
                if not sales_df.empty and "Sales" in sales_df.columns and p.product_id in sales_df.index:
                    try:
                        s = float(sales_df.loc[p.product_id, "Sales"])
                    except Exception:
                        s = 0.0
                out_total += float(getattr(p, "footprint", 0.0)) * s

            scope23 = max(out_total - scope1, 0.0)
            rows.append({
                "Company": cname,
                "Scope 1 (direct) (kg CO2e)": scope1,
                "Scope 2–3 (indirect) (kg CO2e)": scope23,
                "Total emissions (kg CO2e)": scope1 + scope23,
                "Update count": int(getattr(comp, "num_updates", 0)),
            })

        df_companies_scopes = pd.DataFrame(rows).sort_values("Company").reset_index(drop=True)
        st.dataframe(df_companies_scopes, use_container_width=True)

    with tabs[1]:
        st.subheader("Footprint evolution by product (kg CO2e/$)")
        if dfp.empty:
            st.info("No history captured.")
        else:
            plist = dfp[["product_id","product_name"]].drop_duplicates().sort_values("product_id")
            default = plist["product_id"].head(8).tolist()
            picks = st.multiselect("Choose products to plot", plist["product_id"].tolist(), default=default)
            plot_df = (
                dfp[dfp["product_id"].isin(picks)]
                .pivot_table(index="iteration", columns="product_id", values="footprint")
                .sort_index()
            )
            if plot_df.empty:
                st.info("Select at least one product with recorded history.")
            else:
                st.line_chart(plot_df, use_container_width=True)
            with st.expander("Show evolution table"):
                st.dataframe(
                    dfp[dfp["product_id"].isin(picks)].sort_values(["product_id","iteration"]),
                    use_container_width=True
                )

    with tabs[2]:
        st.subheader("Per-company carbon balance (final)")
        rows = []
        for cname, comp in system.companies.items():
            # replicate Company.check_carbon_balance maths, but structured
            df24 = fp_db.data.copy()
            df24["id"] = df24["id"].astype(int)
            scores = df24.set_index("id")["scores"]

            pur = comp.purchases if isinstance(comp.purchases, pd.Series) else pd.Series(dtype=float)
            if not pur.empty:
                try:
                    pur.index = pur.index.astype(int)
                except Exception:
                    pur = pd.Series(dtype=float)
            common = pur.index.intersection(scores.index)
            in_emb = float(pur.loc[common] @ scores.loc[common]) if len(common) else 0.0

            in_dir = 0.0
            if isinstance(comp.direct_impacts, pd.DataFrame) and not comp.direct_impacts.empty:
                try:
                    in_dir = float(comp.direct_impacts.sum(numeric_only=True).sum())
                except Exception:
                    in_dir = 0.0
            in_total = in_emb + in_dir

            out_total = 0.0
            if isinstance(comp.sales, pd.DataFrame) and "Sales" in comp.sales.columns:
                for p in comp.products:
                    if p.product_id in comp.sales.index:
                        try:
                            s = float(comp.sales.loc[p.product_id, "Sales"])
                        except Exception:
                            s = 0.0
                        out_total += float(getattr(p, "footprint", 0.0)) * s

            rows.append({
                "Company": cname,
                "in_emb": in_emb, "in_direct": in_dir, "in_total": in_total,
                "out_total": out_total,
                "delta": out_total - in_total,
                "balanced": bool(np.isclose(out_total, in_total, atol=1e-2)),
            })
        df_bal = pd.DataFrame(rows)
        if df_bal.empty:
            st.info("No balance rows.")
        else:
            fmt = df_bal.copy()
            for c in ["in_emb","in_direct","in_total","out_total","delta"]:
                fmt[c] = fmt[c].map(lambda x: f"{x:.6f}")
            st.dataframe(fmt, use_container_width=True)
            bad = df_bal[~df_bal["balanced"]]
            if not bad.empty:
                st.warning(f"{len(bad)} companies not balanced within tolerance (1e-2).")

    with tabs[3]:
        st.subheader("Logs")
        if verbose:
            st.code(logs, language="text")
        else:
            st.caption("Verbose logging is off. Enable it in the sidebar to see solver prints.")

# ------------- main -------------
if run_btn:
    class_db = _load_classification_db(up_class)
    if class_db is None:
        st.stop()

    if add_demo and sec_unit is None:
        st.error("Cannot add a co-product because the classification code could not be resolved.")
        st.stop()

    sales_db = _build_last_year_sales(2023, class_db, up_sales)
    if sales_db is None:
        st.stop()
    st.caption(
        "**Note:** Last-year market sales are loaded for substitution averages. The full dataset is not displayed here.")

    with st.spinner("Running substitution solver…"):
        logs, dfc, dfp, dfh, system, fp_db = run_simulation(
            size=size, seed=seed, max_iter=max_iter, verbose=verbose,
            classification_db=class_db,
            add_demo_secondary=add_demo, demo_company_idx=demo_company_idx,
            sec_name=sec_name, sec_class=sec_class, sec_unit=sec_unit,
            sec_function_output=sec_function_output, sec_sales=sec_sales,
            last_year_sales=sales_db,
        )

    st.session_state["subst_sim"] = {
        "logs": logs, "dfc": dfc, "dfp": dfp, "dfh": dfh, "system": system, "fp_db": fp_db,
        "params": dict(size=size, seed=seed, max_iter=max_iter, verbose=verbose),
    }
    st.success("Done.")
    render_results(system, dfc, dfp, logs, verbose, fp_db)

elif "subst_sim" in st.session_state:
    saved = st.session_state["subst_sim"]
    render_results(saved["system"], saved["dfc"], saved["dfp"], saved["logs"], saved["params"]["verbose"], saved["fp_db"])
else:
    st.info("Upload (or use demo) classification & last-year sales, set options, then click **Run substitution simulation**.")
