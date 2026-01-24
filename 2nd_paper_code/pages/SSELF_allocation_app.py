# pages/SSELF_allocation_app.py
import os, sys, io, random
import numpy as np
import pandas as pd
import streamlit as st
from contextlib import redirect_stdout

# ----- locate code package -----
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODE_DIR = os.path.join(BASE_DIR, "SSELF_python_code")
if CODE_DIR not in sys.path:
    sys.path.insert(0, CODE_DIR)

from SSELF_base import FootprintDatabase
from allocation_extension import (
    AllocationProduct,
    AllocationHierarchicalCompany,
    AllocationHierarchicalSystem,
)
from SSELF_base import FootprintDatabase


st.set_page_config(page_title="SSELF — Allocation Extension", layout="wide")
st.title("SSELF — Allocation Extension (choose your allocation)")

def _next_free_pid(sys_obj):
    """Return a fresh product ID not used anywhere in the system and advance the counter."""
    used = set()
    for comp in sys_obj.companies.values():
        for p in getattr(comp, "products", []):
            used.add(int(p.product_id))
        for sub in getattr(comp, "sub_companies", []):
            for p in getattr(sub, "products", []):
                used.add(int(p.product_id))

    # next unused id: at least max(used)+1 and at least current counter
    next_id = (max(used) + 1) if used else 1
    if hasattr(sys_obj, "product_id_counter"):
        next_id = max(next_id, int(sys_obj.product_id_counter))

    # advance counter for future calls
    sys_obj.product_id_counter = int(next_id) + 1
    return int(next_id)


# --- Impact balance helpers (allocation version) ---
def _iter_all_entities(system):
    for comp in system.companies.values():
        yield comp
        for sub in getattr(comp, "sub_companies", []):
            yield sub

def build_balance_df(system, fp_db, atol=1e-6):
    rows = []
    db = fp_db.data.copy()
    if not db.empty:
        db["id"] = db["id"].astype(int)
        db = db.set_index("id")

    for ent in _iter_all_entities(system):
        # Inbound embedded impacts from purchases
        in_emb = 0.0
        if isinstance(ent.purchases, pd.Series) and not ent.purchases.empty and not db.empty:
            # align purchases to DB ids; missing IDs → treated as 0
            try:
                vec = ent.purchases.astype(float)
                vec.index = vec.index.astype(int)
                common = vec.index.intersection(db.index)
                if len(common) > 0:
                    in_emb = float((db.loc[common, "scores"].astype(float) * vec.loc[common]).sum())
            except Exception:
                in_emb = 0.0

        # Direct impacts
        in_direct = 0.0
        if isinstance(ent.direct_impacts, pd.DataFrame) and not ent.direct_impacts.empty:
            try:
                in_direct = float(ent.direct_impacts.sum(numeric_only=True).sum())
            except Exception:
                in_direct = 0.0

        in_total = in_emb + in_direct

        # Outbound: sum over products of (footprint * sales)
        out_total = 0.0
        sales_df = ent.sales if isinstance(ent.sales, pd.DataFrame) else pd.DataFrame()
        for p in getattr(ent, "products", []):
            s_val = 0.0
            if not sales_df.empty and "Sales" in sales_df.columns and p.product_id in sales_df.index:
                try:
                    s_val = float(sales_df.loc[p.product_id, "Sales"])
                except Exception:
                    s_val = 0.0
            out_total += float(getattr(p, "footprint", 0.0)) * s_val

        delta = in_total - out_total
        rows.append({
            "entity": getattr(ent, "name", "<?>"),
            "in_emb": in_emb,
            "in_direct": in_direct,
            "in_total": in_total,
            "out_total": out_total,
            "delta": delta,
            "balanced": (abs(delta) <= float(atol)),
        })

    return pd.DataFrame(rows)

def balance_summary(df: pd.DataFrame):
    if df is None or df.empty:
        return {"sum_in_total": 0.0, "sum_out_total": 0.0, "sum_delta": 0.0, "all_balanced": True}
    s_in = float(df["in_total"].sum())
    s_out = float(df["out_total"].sum())
    s_delta = float(df["delta"].sum())
    all_bal = bool(df["balanced"].all()) if "balanced" in df.columns else False
    return {"sum_in_total": s_in, "sum_out_total": s_out, "sum_delta": s_delta, "all_balanced": all_bal}

# ---------- helper: progress hook that alos walks sub-companies----------
def _make_progress_hook(system):
    if not hasattr(system, "_ui_history"):
        system._ui_history = []

    def _emit_for_entity(iter_idx, ent):
        for p in getattr(ent, "products", []):
            system._ui_history.append({
                "iteration": int(iter_idx),
                "company": ent.name,
                "product_id": int(p.product_id),
                "product_name": getattr(p, "name", f"Product {p.product_id}"),
                "footprint": float(getattr(p, "footprint", float("nan"))),
                "latest_update": (
                    float(getattr(ent, "latest_update", float("nan")))
                    if getattr(ent, "latest_update", None) is not None else float("nan")
                ),
            })

    def hook(iter_idx, sys_obj):
        setattr(sys_obj, "current_iteration", int(iter_idx))
        for comp in sys_obj.companies.values():
            _emit_for_entity(iter_idx, comp)
            for sub in getattr(comp, "sub_companies", []):
                _emit_for_entity(iter_idx, sub)

    return hook


# --------------------------- Sidebar ---------------------------
with st.sidebar:
    st.header("Run settings")
    size = st.number_input("System size (companies = products)", 1, 500, 10, step=1)
    seed = st.number_input("Random seed (optional)", value=42, step=1)
    verbose = st.checkbox("Verbose logs", value=True)

    # ---- Allocation method (no Auto) ----
    st.markdown("**Allocation method**")
    alloc_choice = st.radio(
        "Preferred basis",
        ["Mass", "Volume", "Energy", "Economic"],
        index=0,
        horizontal=True,
        label_visibility="collapsed",
    )

    demo = st.checkbox("Custom co-products for a sub-company under Company_1", value=True)

    can_run = True
    prop_key = {"Mass": "mass", "Volume": "volume", "Energy": "energy", "Economic": None}[alloc_choice]
    prop_label = {"mass": "Mass", "volume": "Volume", "energy": "Energy"}.get(prop_key, "")

    if demo:
        st.markdown("**Co-products (edit rows as needed)**")

        # ensure an editor exists
        if "alloc_coprods" not in st.session_state:
            st.session_state.alloc_coprods = pd.DataFrame([
                {"name": "Copper coil", "sales": 300.0, "mass": 600.0, "volume": 0.0, "energy": 0.0},
                {"name": "Copper residue", "sales": 200.0, "mass": 400.0, "volume": 0.0, "energy": 0.0},
            ])

        # Column config: show only the selected property column (if not Economic)
        col_cfg = {
            "name": st.column_config.TextColumn("Product name", required=True),
            "sales": st.column_config.NumberColumn("Sales ($)", min_value=0.0, step=10.0, format="%.3f"),
            "mass": st.column_config.NumberColumn("Mass", min_value=0.0, step=1.0, format="%.3f"),
            "volume": st.column_config.NumberColumn("Volume", min_value=0.0, step=1.0, format="%.3f"),
            "energy": st.column_config.NumberColumn("Energy", min_value=0.0, step=1.0, format="%.3f"),
        }
        # Hide non-relevant property columns visually by reordering and describing expectations
        visible_cols = ["name", "sales"]
        if prop_key is not None:
            visible_cols.append(prop_key)

        coprods_df = st.data_editor(
            st.session_state.alloc_coprods[visible_cols]  # show relevant cols
            .reindex(columns=visible_cols),
            num_rows="dynamic",
            use_container_width=True,
            key="alloc_coprods_editor",
            column_config={k: v for k, v in col_cfg.items() if k in visible_cols},
        )

        # Keep full copy in session (including hidden props) so we don’t lose data
        # Merge back into the stored DataFrame
        stored = st.session_state.alloc_coprods.copy()
        for col in visible_cols:
            stored[col] = coprods_df[col].values
        st.session_state.alloc_coprods = stored

        # Purchases and direct impacts
        st.markdown("**Sub-company purchases (from world product IDs)**")
        if "alloc_sub_purchases" not in st.session_state:
            st.session_state.alloc_sub_purchases = pd.DataFrame({
                "product_id": [1, 2, 3],
                "amount": [100.0, 100.0, 100.0],
            })
        sub_pur_df = st.data_editor(
            st.session_state.alloc_sub_purchases,
            num_rows="dynamic",
            use_container_width=True,
            key="alloc_sub_pur_editor",
            column_config={
                "product_id": st.column_config.NumberColumn("Product ID", min_value=1, step=1),
                "amount": st.column_config.NumberColumn("Amount", min_value=0.0, step=10.0, format="%.3f"),
            },
        )
        st.session_state.alloc_sub_purchases = sub_pur_df

        direct_val = st.number_input("Sub-company direct impacts (kg CO₂e)", min_value=0.0, value=100.0, step=10.0)

        # ---- Validation (block run if invalid) ----
        if prop_key is not None:
            total_prop = float(st.session_state.alloc_coprods.get(prop_key, pd.Series(dtype=float)).fillna(0.0).sum())
            if total_prop <= 0:
                st.error(f"{prop_label} allocation selected, but total {prop_label.lower()} is 0. "
                         f"Enter positive {prop_label.lower()} values for at least one co-product.")
                can_run = False

        # At least one co-product with non-empty name and (sales > 0)
        if st.session_state.alloc_coprods["name"].fillna("").str.strip().eq("").all():
            st.error("Please provide at least one co-product name.")
            can_run = False
        if (st.session_state.alloc_coprods.get("sales", pd.Series(dtype=float)).fillna(0.0) <= 0).all():
            st.error("Please provide Sales ($) > 0 for at least one co-product.")
            can_run = False

        # stash for run
        st.session_state["alloc_demo_inputs"] = {
            "coprods": st.session_state.alloc_coprods.copy(),
            "purchases": st.session_state.alloc_sub_purchases.copy(),
            "direct": float(direct_val),
        }
    else:
        st.session_state.pop("alloc_demo_inputs", None)

    # Map chosen method to system.preferred_basis later

    col_run, col_clear = st.columns(2)
    run_btn = col_run.button("Run allocation simulation", type="primary")
    clear_btn = col_clear.button("Clear results")

if clear_btn:
    st.session_state.pop("alloc_results", None)
    st.rerun()

# --------------------------- Core run ---------------------------
def run_simulation(size:int, seed:int, verbose:bool, alloc_choice:str, demo:bool, demo_inputs=None):
    # RNG
    random.seed(int(seed)); np.random.seed(int(seed))

    # Build system
    system = AllocationHierarchicalSystem(num_companies=size, num_products=size)
    basis_map = {"Mass": "mass", "Volume": "volume", "Energy": "energy", "Economic": "economic"}
    system.preferred_basis = basis_map[alloc_choice]

    # Optional demo coproducts on Company_1
    if demo and "Company_1" in system.companies and demo_inputs:
        root = system.companies["Company_1"]

        # --- co-products table ---
        coprods = demo_inputs.get("coprods", pd.DataFrame()).copy()
        coprods = coprods.fillna(0.0)

        # keep only meaningful rows (nonempty name and any positive sales or properties)
        def _row_ok(r):
            return str(r.get("name", "")).strip() != "" and (
                    float(r.get("sales", 0)) > 0 or
                    float(r.get("mass", 0)) > 0 or
                    float(r.get("volume", 0)) > 0 or
                    float(r.get("energy", 0)) > 0
            )

        coprods = coprods[[_row_ok(r) for _, r in coprods.iterrows()]]
        coprods = coprods.reset_index(drop=True)

        # --- purchases table (dedupe product_id to avoid pandas reindex error) ---
        sub_pur_df = demo_inputs.get("purchases", pd.DataFrame(columns=["product_id", "amount"])).copy()

        if not sub_pur_df.empty:
            # clean
            sub_pur_df = sub_pur_df.dropna(subset=["product_id"])
            sub_pur_df["product_id"] = pd.to_numeric(sub_pur_df["product_id"], errors="coerce")
            sub_pur_df["amount"] = pd.to_numeric(sub_pur_df["amount"], errors="coerce").fillna(0.0)

            # keep only valid ids
            sub_pur_df = sub_pur_df[sub_pur_df["product_id"].notnull()]
            sub_pur_df["product_id"] = sub_pur_df["product_id"].astype(int)

            # aggregate duplicates → UNIQUE index required by hierarchy solver
            sub_pur_agg = (
                sub_pur_df.groupby("product_id", as_index=True)["amount"]
                .sum()
                .sort_index()
                .astype(float)
            )
            sub_purchases = pd.Series(sub_pur_agg.values, index=sub_pur_agg.index, dtype=float)

            # (optional) user hint if duplicates were present
            if sub_pur_df["product_id"].duplicated(keep=False).any():
                st.warning("Duplicate Product IDs in sub-company purchases were aggregated (summed).")
        else:
            sub_purchases = pd.Series(dtype=float)

        sub_direct = float(demo_inputs.get("direct", 0.0))
        sub_direct_df = pd.DataFrame({"kg CO2eq": [sub_direct]})

        # --- build sub-company ---
        sub = AllocationHierarchicalCompany("Company_1_Sub_1", 2024, sub_purchases, pd.DataFrame(), sub_direct_df)
        sub.system = system
        # then add AllocationProduct(...) for each coproduct and set sub.sales
        root.add_sub_company(sub)

        # create products with sequential new IDs; collect sales
        sales_map = {}
        for _, row in coprods.iterrows():
            pid = _next_free_pid(system)
            name = str(row.get("name", f"Product {pid}"))
            sales = float(row.get("sales", 0.0))
            props = {
                "mass": float(row.get("mass", 0.0)),
                "volume": float(row.get("volume", 0.0)),
                "energy": float(row.get("energy", 0.0)),
            }
            p = AllocationProduct(pid, name, "unit", sub, properties=props)
            sub.add_product(p)
            sales_map[pid] = sales

        # set sales df for sub-company
        if sales_map:
            sub.sales = pd.DataFrame.from_dict(sales_map, orient="index", columns=["Sales"])

        # attach sub to the root company
        root.add_sub_company(sub)

    # Seed DB with zeroes for all visible products (root + subs)
    fp_db = FootprintDatabase(2024)
    for c in system.companies.values():
        for p in c.products:
            fp_db.report(int(p.product_id), 0.0)
        for sub in getattr(c, "sub_companies", []):
            for p in sub.products:
                fp_db.report(int(p.product_id), 0.0)

    system._ui_history = []
    hook = _make_progress_hook(system)  # make sure it emits both top-level and subs

    f = io.StringIO()
    with redirect_stdout(f):
        system.solve(
            fp_db,
            verbose=verbose,
            random_state=seed,  # randomized scheduling per iteration, reproducible
            max_iter=5000,
            progress_callback=hook,
        )
    logs = f.getvalue()

    # Build Overview/Evolution frames from history
    df_hist = pd.DataFrame(system._ui_history)
    df_companies = (
        df_hist.groupby(["company", "iteration"], as_index=False)
               .agg(latest_update=("latest_update", "last"))
        if not df_hist.empty else pd.DataFrame(columns=["company","iteration","latest_update"])
    )
    df_products = (
        df_hist.groupby(["product_id", "product_name", "iteration"], as_index=False)
               .agg(footprint=("footprint", "last"))
        if not df_hist.empty else pd.DataFrame(columns=["product_id","product_name","iteration","footprint"])
    )
    return logs, df_companies, df_products, df_hist, system, fp_db


# --------------------------- Results UI ---------------------------
def render_results(system, dfc, dfp, logs, verbose, fp_db=None):
    tabs = st.tabs(["Overview", "Per-product evolution", "Proof check: Impact balance", "Logs"])

    with tabs[0]:
        st.caption(f"Current iteration: {getattr(system, 'current_iteration', 0)}")
        left, right = st.columns([1, 1])

        with left:
            st.subheader("Product carbon footprint results")
            if not dfp.empty:
                last_iter = dfp["iteration"].max()
                st.caption(f"Iteration {last_iter}")
                df_products_final = dfp[dfp["iteration"] == last_iter].copy()
                df_products_final["unit"] = "kg CO2e/$"
                pid2company = {}
                for cname, comp in system.companies.items():
                    for p in comp.products:
                        pid2company[p.product_id] = cname
                    for sub in getattr(comp, "sub_companies", []):
                        for p in sub.products:
                            pid2company[p.product_id] = sub.name
                df_products_final["Company"] = df_products_final["product_id"].map(pid2company)
                df_products_final = df_products_final.rename(columns={
                    "product_id": "Product ID",
                    "product_name": "Product name",
                    "footprint": "Footprint score",
                    "unit": "Unit",
                })[["Company", "Product ID", "Product name", "Footprint score", "Unit"]]
                st.dataframe(df_products_final.reset_index(drop=True), use_container_width=True)
            else:
                st.info("No product results.")

        with right:
            st.subheader("Company GHG results (final, split by scope)")
            rows = []
            for cname, comp in system.companies.items():
                # include sub-companies in totals
                def iter_all(co):
                    yield co
                    for s in getattr(co, "sub_companies", []):
                        yield s
                scope1 = 0.0; total_out = 0.0
                for ent in iter_all(comp):
                    if getattr(ent, "direct_impacts", None) is not None and not ent.direct_impacts.empty:
                        try: scope1 += float(ent.direct_impacts.sum(numeric_only=True).sum())
                        except Exception: pass
                    sales_df = ent.sales if isinstance(ent.sales, pd.DataFrame) else pd.DataFrame()
                    for p in getattr(ent, "products", []):
                        if not sales_df.empty and "Sales" in sales_df.columns and p.product_id in sales_df.index:
                            try: s_val = float(sales_df.loc[p.product_id, "Sales"])
                            except Exception: s_val = 0.0
                            total_out += float(getattr(p, "footprint", 0.0)) * s_val
                scope23 = max(total_out - scope1, 0.0)
                rows.append({"Company": cname, "Scope 1 (kg CO2e)": scope1, "Scope 2–3 (kg CO2e)": scope23})
            st.dataframe(pd.DataFrame(rows), use_container_width=True)

    with tabs[1]:
        st.subheader("Footprint evolution by product (kg CO2e/$)")
        if dfp.empty:
            st.info("No data yet.")
        else:
            product_list = dfp[["product_id", "product_name"]].drop_duplicates().sort_values("product_id")
            default_choices = st.session_state.get("alloc_selected_products", product_list["product_id"].head(8).tolist())
            choices = st.multiselect(
                "Choose products to plot",
                product_list["product_id"].tolist(),
                default=default_choices,
                key="alloc_selected_products",
            )
            plot_df = (
                dfp[dfp["product_id"].isin(choices)]
                .pivot_table(index="iteration", columns="product_id", values="footprint")
                .sort_index()
            )
            st.line_chart(plot_df, use_container_width=True)
            with st.expander("Show evolution table"):
                st.dataframe(
                    dfp[dfp["product_id"].isin(choices)].sort_values(["product_id","iteration"]),
                    use_container_width=True,
                )


    # ---- Impact balance proof check ----
    with tabs[2]:
        st.subheader("Proof check: Per-entity impact balance")

        if fp_db is None:
            st.warning("No footprint database available.")
        else:
            try:
                dfb = build_balance_df(system, fp_db, atol=1e-6)
                if dfb.empty:
                    st.info("No balance rows produced.")
                else:
                    dfb_disp = dfb.copy()
                    for c in ["in_emb", "in_direct", "in_total", "out_total", "delta"]:
                        if c in dfb_disp.columns:
                            dfb_disp[c] = pd.to_numeric(dfb_disp[c], errors="coerce").map(
                                lambda x: f"{x:.6f}" if pd.notnull(x) else ""
                            )
                    st.dataframe(dfb_disp.reset_index(drop=True), use_container_width=True)

                    summ = balance_summary(dfb)
                    st.markdown(
                        f"**Summary**  \n"
                        f"- Sum(in_total): `{summ['sum_in_total']:.6f}`  \n"
                        f"- Sum(out_total): `{summ['sum_out_total']:.6f}`  \n"
                        f"- Sum(delta): `{summ['sum_delta']:.6f}`  \n"
                        f"- All entities balanced: `{summ['all_balanced']}`"
                    )

                    if "balanced" in dfb.columns and not dfb["balanced"].all():
                        st.warning(
                            "Some entities are not balanced within tolerance. "
                            "Check purchases indexing, missing DB scores, sales values, or property-based allocation inputs."
                        )
            except Exception as e:
                st.error("Impact balance computation failed.")
                st.exception(e)

    with tabs[3]:
        st.subheader("Logs")
        if verbose:
            st.code(logs, language="text")
        else:
            st.caption("Verbose logging is off. Enable it in the sidebar to see solver prints.")
# --------------------------- Run flow ---------------------------
if run_btn:
    with st.spinner("Running allocation simulation…"):
        logs, dfc, dfp, dfh, system, fp_db = run_simulation(
            size=int(size),
            seed=int(seed),
            verbose=verbose,
            alloc_choice=alloc_choice,
            demo=demo,
            demo_inputs=st.session_state.get("alloc_demo_inputs"),  # 👈 NEW
        )

    st.session_state["alloc_results"] = {
        "logs": logs, "dfc": dfc, "dfp": dfp, "dfh": dfh, "system": system, "fp_db": fp_db,
        "params": {"size": size, "seed": seed, "verbose": verbose, "alloc_choice": alloc_choice}
    }

    st.success("Done.")
    render_results(system, dfc, dfp, logs, verbose, fp_db)
    # and
    render_results(saved["system"], saved["dfc"], saved["dfp"], saved["logs"], saved["params"]["verbose"],
                   saved.get("fp_db"))
elif "alloc_results" in st.session_state:
    saved = st.session_state["alloc_results"]
    render_results(saved["system"], saved["dfc"], saved["dfp"], saved["logs"], saved["params"]["verbose"])
else:
    st.info("Set parameters in the sidebar and click **Run allocation simulation**.")
