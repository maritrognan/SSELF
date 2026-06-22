#SSELF_base_app.py
import os, sys, io, random, time
import numpy as np
import pandas as pd
import streamlit as st
from contextlib import redirect_stdout

class _StopForApproval(Exception):
    """Raised to pause the solver when a manual approval is queued."""
    pass

def _make_progress_hook(system):
    """Return a hook that appends per-iteration snapshots to system._ui_history."""
    # ensure the history list exists
    if not hasattr(system, "_ui_history"):
        system._ui_history = []

    def hook(iter_idx, sys_obj):
        # expose current iteration (for Approvals and logs)
        setattr(sys_obj, "current_iteration", int(iter_idx))
        # snapshot product footprints
        for cname, comp in sys_obj.companies.items():
            for p in comp.products:
                sys_obj._ui_history.append({
                    "iteration": int(iter_idx),
                    "company": cname,
                    "product_id": p.product_id,
                    "product_name": getattr(p, "name", f"Product {p.product_id}"),
                    "footprint": float(getattr(p, "footprint", float("nan"))),
                    "latest_update": (
                        float(getattr(comp, "latest_update", float("nan")))
                        if getattr(comp, "latest_update", None) is not None else float("nan")
                    ),
                })
    return hook


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # one level up
CODE_DIR = os.path.join(BASE_DIR, "SSELF_python_code")
if CODE_DIR not in sys.path:
    sys.path.insert(0, CODE_DIR)

from SSELF_base import System, FootprintDatabase

st.set_page_config(page_title="SSELF Base Simulation", layout="wide")
st.title("SSELF — Base Simulation (prototype)")

with st.sidebar:
    st.header("Simulation Settings")

    # ---- Mode switch ----
    mode = st.selectbox(
        "Mode",
        ["Classic (random world)", "Company-in-the-world (beta)"],
        index=0,
        help="Classic runs the fully synthetic world. Company-in-the-world lets you enter your own purchases/sales/emissions (local only) and simulates the rest."
    )

    size = st.number_input("System size (companies = products)", 1, 500, 10, step=1)
    seed = st.number_input("Random seed (optional)", value=42, step=1)
    verbose = st.checkbox("Verbose logs", value=True)

    # ---- Company-in-the-world inputs (conditional) ----
    user_payload = None
    auto_update = True  # default

    if mode == "Company-in-the-world (beta)":
        st.markdown("---")
        st.subheader("Your company (local data)")
        st.caption("Your purchases, sales, and emissions stay on this machine. They are not uploaded or shared.")

        user_company_name = st.text_input("Company name", "YourCo")
        user_product_name = st.text_input("Product name", "YourCo Product")

        # --- Purchases editor: default rows = product IDs 1..size (amounts default 0)
        st.markdown("**Purchases (product IDs you buy → amounts)**")
        # Rebuild default purchases if not there OR if system size changed
        if ("_ui_purchases_size" not in st.session_state) or (st.session_state.get("_ui_purchases_size") != int(size)):
            st.session_state.ui_purchases = pd.DataFrame(
                {"product_id": list(range(1, int(size) + 1)), "amount": [0.0] * int(size)}
            )
            st.session_state["_ui_purchases_size"] = int(size)

        ui_purchases = st.data_editor(
            st.session_state.ui_purchases,
            num_rows="dynamic",
            use_container_width=True,
            key="ui_purchases_editor",
        )

        # --- Direct emissions editor (single scalar)
        st.markdown("**Direct emissions (kg CO2e)**")
        if "ui_emissions" not in st.session_state:
            st.session_state.ui_emissions = pd.DataFrame({"kg CO2eq": [0.0]})
        ui_emissions = st.data_editor(
            st.session_state.ui_emissions,
            use_container_width=True,
            key="ui_emissions_editor",
        )

        st.markdown("**Sales (your single product)**")

        # Fixed Product ID (small, read-only field)
        default_pid = int(st.session_state.get("_ui_sales_pid", 1))
        col_pid, col_sales = st.columns([1, 3])

        with col_pid:
            st.text_input(
                "Product ID",
                value=str(default_pid),
                disabled=True,  # no +/-; not editable
                help="Your product's ID in the world (fixed).",
            )

        with col_sales:
            sales_val = float(st.session_state.get("_ui_sales_value", 0.0))
            sales_val = st.number_input("Total sales ($)", min_value=0.0, value=sales_val, step=10.0)
            st.session_state["_ui_sales_value"] = sales_val

        # Keep downstream shape the same
        st.session_state.ui_sales = pd.DataFrame({"product_id": [default_pid], "Sales": [sales_val]})
        ui_sales = st.session_state.ui_sales

        # Update policy
        st.markdown("**Update policy**")
        auto_update = st.radio(
            "When an update is warranted for your products…",
            ["Perform updates automatically", "Queue for manual approval"],
            index=0,
        ) == "Perform updates automatically"

        # Package the user inputs for run_simulation
        user_payload = {
            "name": user_company_name,
            "product_name": user_product_name,  # 👈 NEW
            "products_count": 1,
            "ui_purchases": ui_purchases,
            "ui_sales": ui_sales,
            "ui_emissions": ui_emissions,
        }

        st.info(
            "**Privacy note**: Your inputs remain local to this app session and are never sent to any external service.")

    # ---- Action buttons ----
    col_run, col_clear = st.columns(2)
    run_btn = col_run.button("Run simulation", type="primary")
    clear_btn = col_clear.button("Clear results")


logs_placeholder = st.empty()   # we’ll render at the bottom after results
result_container = st.container()

def run_simulation(
    size,
    seed,
    verbose=True,
    mode="Classic (random world)",
    user_company=None,        # {"name", "products_count", "ui_purchases", "ui_sales", "ui_emissions"} or None
    auto_update=True,         # True = perform updates immediately; False = queue approvals
):
    # --- Reproducibility
    if seed is not None:
        random.seed(int(seed))
        np.random.seed(int(seed))

    # --- Build world + seed DB
    system = System(num_companies=size, num_products=size)
    fp_2024 = FootprintDatabase(year=2024)
    setattr(system, "_ui_fp_db", fp_2024)  # store DB on the system so we can resume later
    # seed all product IDs to 0.0 (same as base behavior)
    for company in system.companies.values():
        for product in company.products:
            fp_2024.report(product.product_id, 0.0)

    # --- If user-company mode: inject user's local data into the world
    user_company_key = None
    if mode == "Company-in-the-world (beta)" and user_company is not None:
        user_company_key, user_co = _apply_user_company(
            system,
            user_company["name"],
            int(user_company["products_count"]),
            user_company["ui_purchases"],
            user_company["ui_sales"],
            user_company["ui_emissions"],
            product_name=user_company.get("product_name", None),  # 👈 NEW
        )

    # flags & queues the UI/framework will use
    setattr(system, "_ui_user_company_key", user_company_key)   # None if classic
    setattr(system, "_ui_auto_update", bool(auto_update))
    if not hasattr(system, "_ui_pending_approvals"):
        system._ui_pending_approvals = []

    # --- Per-iteration history: stored on system so we can resume later
    system._ui_history = []  # reset every fresh run
    hook = _make_progress_hook(system)

    # --- Manual-approval callback (only used when auto_update=False)
    def _approval_callback(company, previous, proposed, footprint_db):
        # enqueue an approval item the UI can show & act on
        system._ui_pending_approvals.append({
            "company": getattr(company, "name", "YourCo"),
            "company_obj": company,
            "previous": float(previous) if previous is not None else 0.0,
            "proposed": float(proposed),
            "iteration": getattr(system, "current_iteration", None),
            "footprint_db": footprint_db,
        })
        # expose the DB and callback for resume runs
        setattr(system, "_ui_fp_db", fp_2024)
        setattr(system, "_ui_approval_callback", _approval_callback)
        # Queue approval, but let the current sweep finish.
        # The solver will pause before starting the next iteration.
        setattr(system, "_ui_pause_after_sweep", True)
        return

    # --- Run solver while capturing logs
    f = io.StringIO()
    with redirect_stdout(f):
        try:
            try:
                system.solve(
                    fp_2024,
                    forced_updates=0,
                    verbose=verbose,
                    progress_callback=hook,  # << always pass hook
                    auto_update=auto_update,
                    approval_callback=(None if auto_update else _approval_callback),
                )
            except TypeError:
                # Older System.solve without policy/callback support
                system.solve(
                    fp_2024,
                    forced_updates=0,
                    verbose=verbose,
                    progress_callback=hook,  # << always pass hook
                )
        except Exception as e:
            # Treat our pause signal as expected, regardless of module identity
            if getattr(e, "__class__", None) and e.__class__.__name__ == "_StopForApproval":
                if verbose:
                    print("[paused] Waiting for manual approval...")
            else:
                raise
    logs = f.getvalue()

    # --- Build final snapshots for UI from system._ui_history
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

    return logs, df_companies, df_products, df_hist, system



def _apply_user_company(system, name, products_count, ui_purchases, ui_sales, ui_emissions, product_name=None):
    """
    Inject the user's local company data into the generated world.
    - Renames (or reuses) one company as `name`
    - Forces exactly ONE product for that company
    - Purchases default to IDs 1..system_size (already set in sidebar), but we align to actual world product IDs
    - Sales is a single number mapped to the company's single product_id
    """
    # Reuse existing company if name present; otherwise rename the first
    if name in system.companies:
        user_co = system.companies[name]
    else:
        first_key = next(iter(system.companies.keys()))
        user_co = system.companies.pop(first_key)
        system.companies[name] = user_co

    # ---- Force exactly ONE product for the user company
    if hasattr(user_co, "products"):
        curr = list(user_co.products)
        if len(curr) >= 1:
            user_co.products = [curr[0]]
        else:
            pass

    # 👇 NEW: set the product's display name (before snapshots/solve)
    if getattr(user_co, "products", []):
        only_product = user_co.products[0]
        if product_name and str(product_name).strip():
            only_product.name = str(product_name).strip()
        else:
            # sensible default if left blank
            only_product.name = f"{name} Product"

    # Collect all product IDs that exist in the world (for safe reindexing of purchases)
    all_pids = []
    for c in system.companies.values():
        for p in c.products:
            all_pids.append(int(p.product_id))
    pid_index = pd.Index(sorted(set(all_pids)), dtype=int)

    # ---- Purchases: Series(index=world product IDs, values=float), aligned to all_pids
    try:
        pur = pd.Series(
            ui_purchases["amount"].astype(float).values,
            index=ui_purchases["product_id"].astype(int).values,
            dtype=float,
        )
    except Exception:
        pur = pd.Series(dtype=float)
    # Align to the universe of product IDs to avoid key errors later
    pur = pur.reindex(pid_index, fill_value=0.0)
    user_co.purchases = pur

    # ---- Direct impacts: 1-row DataFrame
    try:
        di_val = float(ui_emissions.iloc[0, 0])
    except Exception:
        di_val = 0.0
    user_co.direct_impacts = pd.DataFrame({"kg CO2eq": [di_val]})

    # ---- Sales: exactly one product → map provided value to the actual product_id
    # Read the single sales value the user entered
    try:
        sales_val = float(pd.DataFrame(ui_sales).iloc[0]["Sales"])
    except Exception:
        sales_val = 0.0

    # Map to the company's single real product ID
    if getattr(user_co, "products", []):
        only_pid = int(user_co.products[0].product_id)
        sales_df = pd.DataFrame({"Sales": [sales_val]}, index=pd.Index([only_pid], name="product_id"))
    else:
        sales_df = pd.DataFrame({"Sales": []})
    user_co.sales = sales_df

    # Mark as user company for policy logic
    setattr(user_co, "is_user_company", True)

    return name, user_co



#To avoid re-runs when using UI to change result view
# To avoid re-runs when using UI to change result view
def render_results(system, dfc, dfp, logs, verbose):
    # Show Approvals tab only when a user company is active
    is_company_mode = getattr(system, "_ui_user_company_key", None) is not None

    tab_labels = ["Overview", "Per-product evolution"]
    if is_company_mode:
        tab_labels.append("Approvals")
    tab_labels.append("Logs")

    tabs = st.tabs(tab_labels)

    # ---- Overview ----
    with tabs[0]:
        st.caption(f"Current iteration: {getattr(system, 'current_iteration', 0)}")
        left, right = st.columns([1, 1])

        # --- Left: Product results ---
        with left:
            st.subheader("Product carbon footprint results")
            if not dfp.empty:
                last_iter = dfp["iteration"].max()
                st.caption(f"Iteration {last_iter}")

                # Final snapshot
                df_products_final = dfp[dfp["iteration"] == last_iter].copy()
                df_products_final["unit"] = "kg CO2e/$"

                # Map product_id -> company name
                pid2company = {}
                for cname, comp in system.companies.items():
                    for p in comp.products:
                        pid2company[p.product_id] = cname

                # Attach company + update count
                df_products_final["Company"] = df_products_final["product_id"].map(pid2company)
                update_counts = {cname: comp.num_updates for cname, comp in system.companies.items()}
                df_products_final["Update count"] = df_products_final["Company"].map(update_counts)

                # Pretty headers + order
                df_products_final = df_products_final.rename(
                    columns={
                        "product_id": "Product ID",
                        "product_name": "Product name",
                        "footprint": "Footprint score",
                        "unit": "Unit",
                    }
                )
                df_products_final = df_products_final[
                    ["Update count", "Product ID", "Product name", "Footprint score", "Unit"]
                ]

                df_products_final = df_products_final.reset_index(drop=True)
                st.dataframe(df_products_final, use_container_width=True)
            else:
                st.info("No product results.")

        # --- Right: Company results (Scopes) ---
        with right:
            st.subheader("Company GHG results (final, split by scope)")
            company_rows = []
            for cname, comp in system.companies.items():
                # Scope 1: direct emissions
                scope1 = float(comp.direct_impacts.sum().sum()) if not comp.direct_impacts.empty else 0.0
                # Scope 2+3: total allocated - scope1
                total_out = 0.0
                for p in comp.products:
                    sales_val = comp.sales.loc[p.product_id, "Sales"] if p.product_id in comp.sales.index else 0
                    total_out += p.footprint * sales_val
                scope23 = max(total_out - scope1, 0.0)
                company_rows.append(
                    {"Company": cname, "Scope 1 (direct) (kg CO2e)": scope1, "Scope 2–3 (indirect) (kg CO2e)": scope23}
                )
            df_companies_scopes = pd.DataFrame(company_rows)
            st.dataframe(df_companies_scopes, use_container_width=True)

    # ---- Per-product evolution ----
    with tabs[1]:
        st.subheader("Footprint evolution by product (kg CO2e/$)")
        if dfp.empty:
            st.info("No data yet.")
        else:
            product_list = dfp[["product_id", "product_name"]].drop_duplicates().sort_values("product_id")
            default_choices = st.session_state.get("selected_products", product_list["product_id"].head(8).tolist())
            choices = st.multiselect(
                "Choose products to plot",
                product_list["product_id"].tolist(),
                default=default_choices,
                key="selected_products",
            )
            plot_df = (
                dfp[dfp["product_id"].isin(choices)]
                .pivot_table(index="iteration", columns="product_id", values="footprint")
                .sort_index()
            )
            st.line_chart(plot_df, use_container_width=True)

            with st.expander("Show evolution table"):
                st.dataframe(
                    dfp[dfp["product_id"].isin(choices)].sort_values(["product_id", "iteration"]),
                    use_container_width=True,
                )

    # ---- Approvals (only in company mode) ----
    if is_company_mode:
        with tabs[2]:
            st.subheader("Pending approvals")
            queue = getattr(system, "_ui_pending_approvals", [])
            st.caption(f"Queue length: {len(queue)}")

            if not queue:
                st.success("No pending approvals.")
            else:
                # Create a stable copy so we can mutate the original (pop) during iteration
                for i, evt in enumerate(list(queue)):
                    with st.expander(f"{evt['company']} — proposed change at iter {evt.get('iteration','?')}"):
                        st.write(f"Previous: {evt['previous']:.6f} → Proposed: {evt['proposed']:.6f}")
                        c1, c2 = st.columns(2)
                        if c1.button(f"Approve #{i + 1}", key=f"approve_{i}"):
                            co = evt["company_obj"]
                            # 1) Apply and publish now
                            co.update_footprint(evt["footprint_db"])
                            co.report_footprint(evt["footprint_db"])
                            co.num_updates = getattr(co, "num_updates", 0) + 1
                            # Remove this approval from the queue
                            system._ui_pending_approvals.pop(i)

                            # 👉 NEW: take an immediate snapshot at the current iteration so Overview/Evolution update right away
                            snap_hook = _make_progress_hook(system)
                            snap_hook(getattr(system, "current_iteration", 0), system)

                            # 2) Immediately continue solving until next approval or convergence
                            fp_db = getattr(system, "_ui_fp_db", None)
                            approval_cb = getattr(system, "_ui_approval_callback", None)
                            if fp_db is not None:
                                # Keep appending to shared history on system AND advance iteration count
                                resume_hook = _make_progress_hook(system)

                                new_logs_buf = io.StringIO()
                                with redirect_stdout(new_logs_buf):
                                    try:
                                        try:
                                            system.solve(
                                                fp_db,
                                                forced_updates=0,
                                                verbose=verbose,
                                                progress_callback=resume_hook,
                                                # << important: update iteration & history
                                                auto_update=getattr(system, "_ui_auto_update", True),
                                                approval_callback=(
                                                    None if getattr(system, "_ui_auto_update", True) else approval_cb),
                                            )
                                        except TypeError:
                                            system.solve(
                                                fp_db,
                                                forced_updates=0,
                                                verbose=verbose,
                                                progress_callback=resume_hook,  # << important
                                            )
                                    except Exception as e:
                                        # Treat our pause signal as expected, regardless of module identity
                                        if getattr(e, "__class__", None) and e.__class__.__name__ == "_StopForApproval":
                                            pass  # paused again at next approval — perfect
                                        else:
                                            st.error(f"Unexpected error while resuming: {e}")
                                            raise

                                # 3) Rebuild UI data from the updated shared history
                                df_hist = pd.DataFrame(system._ui_history)

                                if df_hist.empty:
                                    new_dfc = pd.DataFrame(columns=["company", "iteration", "latest_update"])
                                    new_dfp = pd.DataFrame(
                                        columns=["product_id", "product_name", "iteration", "footprint"])
                                else:
                                    new_dfc = (df_hist.groupby(["company", "iteration"], as_index=False)
                                               .agg(latest_update=("latest_update", "last")))
                                    new_dfp = (
                                        df_hist.groupby(["product_id", "product_name", "iteration"], as_index=False)
                                        .agg(footprint=("footprint", "last")))

                                # 4) Update cached results so Overview/Evolution refresh
                                try:
                                    st.session_state["sim_results"]["logs"] += "\n" + new_logs_buf.getvalue()
                                    st.session_state["sim_results"]["dfh"] = df_hist
                                    st.session_state["sim_results"]["dfc"] = new_dfc
                                    st.session_state["sim_results"]["dfp"] = new_dfp
                                except Exception:
                                    pass

                            st.rerun()

    # ---- Logs ----
    # Index of Logs tab depends on whether Approvals exists
    logs_tab_idx = 3 if is_company_mode else 2
    with tabs[logs_tab_idx]:
        st.subheader("Logs")
        if verbose:
            st.code(logs, language="text")
        else:
            st.caption("Verbose logging is off. Enable it in the sidebar to see solver prints.")

if run_btn:
    with st.spinner("Running simulation..."):
        logs, dfc, dfp, dfh, system = run_simulation(
            size=size,
            seed=seed,
            verbose=verbose,
            mode=mode,                  # 👈 from the sidebar selectbox
            user_company=user_payload,  # 👈 packaged from sidebar editors
            auto_update=auto_update,    # 👈 radio button (True/False)
        )

    # cache results so UI changes (like multiselect) don't wipe them
    st.session_state["sim_results"] = {
        "logs": logs,
        "dfc": dfc,
        "dfp": dfp,
        "dfh": dfh,
        "system": system,
        "params": {
            "size": size,
            "seed": seed,
            "verbose": verbose,
            "mode": mode,
            "auto_update": auto_update,
        },
    }
    st.success("Done.")
    render_results(system, dfc, dfp, logs, verbose)


elif "sim_results" in st.session_state:
    saved = st.session_state["sim_results"]
    render_results(saved["system"], saved["dfc"], saved["dfp"], saved["logs"], saved["params"]["verbose"])

else:
    st.info("Set your parameters in the sidebar and click **Run simulation**.")
