# pages/SSELF_G2G_region_app.py
import os, sys, io
import pandas as pd
import streamlit as st
from contextlib import redirect_stdout

# ===== Locate your code package =====
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # up to project root
CODE_DIR = os.path.join(BASE_DIR, "SSELF_python_code")
if CODE_DIR not in sys.path:
    sys.path.insert(0, CODE_DIR)

# --- Core frameworks ---
from SSELF_base import FootprintDatabase
from hierarchy_extension import HierarchicalSystem
from substitution_extension import ClassificationDatabase

# --- Your G2G extension module ---
from G2G_extension_region import (
    build_region_average_rule_catalog,
    InMemoryAggregatedMarketDatabase,
    GateToGraveCalculator,
    G2GProduct,
    ParamSource,
)

st.set_page_config(page_title="SSELF — G2G extension", layout="wide")
st.title("SSELF — Gate-to-Grave extesion (Region-average prototype)")

# --------------------------- Sidebar ---------------------------
with st.sidebar:
    st.header("Inputs")
    up_products = st.file_uploader("Products CSV", type=["csv"], key="g2g_products_csv")
    up_companies = st.file_uploader("Companies CSV", type=["csv"], key="g2g_companies_csv")

    st.divider()
    st.caption("…or file paths (optional)")
    products_path = st.text_input("Products path", value="")
    companies_path = st.text_input("Companies path", value="")

    st.divider()
    st.header("Run settings")
    verbose = st.checkbox("Verbose logs", value=True)
    max_iter = st.number_input("Max iterations", min_value=100, max_value=20000, value=5000, step=100)

    st.divider()
    st.header("G2G settings")
    year = st.number_input("Year", min_value=1900, max_value=2100, value=2024, step=1)
    region_id = st.selectbox("Region where product is sold", ["EU", "CA"], index=0)

    st.caption("Region-average background data is loaded internally (not editable in this version).")
    with st.expander("Advanced (debug only)", expanded=False):
        st.caption("Optional: keep dummy intensities for development only.")
        mk_retail = st.number_input("MK_RETAIL_AVG_PER_ITEM", value=0.5, step=0.1)
        mk_detergent = st.number_input("MK_DETERGENT_KG", value=2.0, step=0.1)
        mk_elec = st.number_input("MK_ELEC_KWH", value=0.25, step=0.05)
        mk_eol = st.number_input("MK_TEXTILE_EOL_KG", value=1.2, step=0.1)

    st.divider()
    run_btn = st.button("Run C2G + G2G", type="primary")
    clear_btn = st.button("Clear results")

if clear_btn:
    st.session_state.pop("g2g_res", None)
    st.session_state.pop("g2g_ui", None)
    st.rerun()


def _read_csvs(up_products, up_companies, products_path, companies_path):
    dfp, dfc = None, None
    if up_products is not None:
        dfp = pd.read_csv(up_products)
    elif products_path.strip():
        dfp = pd.read_csv(products_path.strip())

    if up_companies is not None:
        dfc = pd.read_csv(up_companies)
    elif companies_path.strip():
        dfc = pd.read_csv(companies_path.strip())

    return dfp, dfc


def _lookup_class_row(class_db, class_code: str):
    """
    Robust lookup for ClassificationDatabase where class_code might be stored as str or int.
    Returns dict or None.
    """
    if class_db is None:
        return None

    cc_str = str(class_code).strip()
    cc_int = None
    try:
        cc_int = int(float(cc_str))  # handles "6109" or "6109.0"
    except Exception:
        pass

    # 1) Dict-like access: class_db[code]
    for key in (cc_str, cc_int):
        if key is None:
            continue
        try:
            return class_db[key]  # __getitem__
        except Exception:
            pass

    # 2) Common method names: get/lookup/resolve/etc.
    for meth in ("get", "lookup", "get_row", "get_by_code", "resolve"):
        if hasattr(class_db, meth):
            fn = getattr(class_db, meth)
            for key in (cc_str, cc_int):
                if key is None:
                    continue
                try:
                    out = fn(key)
                    if out:
                        return out
                except Exception:
                    pass

    # 3) Common internal attributes (covers lots of simple implementations)
    for attr in ("data", "_data", "_rows", "rows", "_class_data", "_class_rows"):
        if hasattr(class_db, attr):
            rows = getattr(class_db, attr)
            if isinstance(rows, dict):
                for key in (cc_str, cc_int):
                    if key is None:
                        continue
                    if key in rows:
                        return rows[key]
            if isinstance(rows, list):
                for r in rows:
                    if str(r.get("class_code", "")).strip() == cc_str:
                        return r
                    if cc_int is not None:
                        try:
                            if int(float(r.get("class_code"))) == cc_int:
                                return r
                        except Exception:
                            pass

    # 4) If it stores a pandas DataFrame internally
    for attr in ("df", "_df", "table", "_table"):
        if hasattr(class_db, attr):
            df = getattr(class_db, attr)
            try:
                # try as index
                if cc_str in df.index:
                    row = df.loc[cc_str]
                    return row.to_dict() if hasattr(row, "to_dict") else dict(row)
                if cc_int is not None and cc_int in df.index:
                    row = df.loc[cc_int]
                    return row.to_dict() if hasattr(row, "to_dict") else dict(row)
                # try as column
                if "class_code" in df.columns:
                    sub = df[df["class_code"].astype(str).str.strip() == cc_str]
                    if len(sub) == 1:
                        return sub.iloc[0].to_dict()
            except Exception:
                pass

    return None


def run_c2g_then_g2g(
    dfp, dfc, year, region_id, verbose=True, max_iter=5000,
    #mk_retail=0.5, mk_detergent=2.0, mk_elec=0.25, mk_eol=1.2
):
    # 1) Build hierarchy system
    system = HierarchicalSystem(num_companies=0, num_products=0)
    system._build_from_frames(dfc.fillna(""), dfp.fillna(""))

    # 2) Create footprint DB and seed zeros
    fp_db = FootprintDatabase(year=int(year))
    for pid in system._product_id_to_code.keys():
        fp_db.report(int(pid), 0.0)

    # 3) Run cradle-to-gate solver (capture logs)
    f = io.StringIO()
    with redirect_stdout(f):
        try:
            system.solve(fp_db, verbose=verbose, max_iter=int(max_iter))
        except TypeError:
            system.solve(fp_db)
    logs = f.getvalue()

    # 4) Build G2G components
    rule_catalog = build_region_average_rule_catalog()

    # NOTE: keep this as your prototype class DB; expand later from file/DB
    classification_data = [
        {
            "class_code": "6109",
            "class_name": "T-shirts, singlets and other vests, knitted or crocheted",
            "function": "Provide upper-body clothing coverage and basic thermal comfort",
            "unit": "use",  # or "wear"
        },
    ]
    class_db = ClassificationDatabase(classification_data)

    market = InMemoryAggregatedMarketDatabase()
    IMPACT_ID = "CO2"

    region_defaults = {
        "EU": {
            "MK_RETAIL_AVG_PER_ITEM": 0.50,
            "MK_DETERGENT_KG": 2.00,
            "MK_ELEC_KWH": 0.25,
            "MK_TEXTILE_EOL_KG": 1.20,
        },
        "CA": {
            "MK_RETAIL_AVG_PER_ITEM": 0.65,
            "MK_DETERGENT_KG": 2.00,
            "MK_ELEC_KWH": 0.12,
            "MK_TEXTILE_EOL_KG": 1.00,
        },
    }

    for reg, vals in region_defaults.items():
        for key, val in vals.items():
            market.set_intensity(key, reg, IMPACT_ID, int(year), float(val))

    calc = GateToGraveCalculator(rule_catalog=rule_catalog, market_db=market, class_db=class_db)
#    setattr(calc, "class_db", class_db)

    return system, fp_db, calc, class_db, logs


def _get_all_products(system):
    entities = system._iter_all_entities() if hasattr(system, "_iter_all_entities") else system.companies.values()
    all_products = []
    for ent in entities:
        for p in getattr(ent, "products", []):
            all_products.append((int(p.product_id), p, ent))
    return all_products


def _infer_class_code_from_product(product_obj):
    # Try common attribute names. If your BaseProduct has one, this will “just work”.
    for attr in ("class_code", "classification_code", "hs_code", "cn_code", "code"):
        if hasattr(product_obj, attr):
            val = getattr(product_obj, attr)
            if val not in (None, "", "nan"):
                return str(val)
    return ""


def _infer_primary_secondary_from_product(product_obj):
    for attr in ("primary_secondary", "is_primary", "primary"):
        if hasattr(product_obj, attr):
            v = getattr(product_obj, attr)
            if isinstance(v, str) and v.lower() in ("primary", "secondary"):
                return v.lower()
            if isinstance(v, bool):
                return "primary" if v else "secondary"
    return "primary"


def _render_bom_editor(key_prefix: str, materials_hint=None):
    """
    Generic BOM/composition editor (universal UI component).
    Returns (df, valid_bool, error_msg)
    """
    st.markdown("#### Composition (BOM-style, mass fractions)")
    st.caption(
        "Enter material mass fractions (must sum to 1.0). "
        "This is a **producer input** only when the resolved rule requests it."
    )

    if materials_hint and isinstance(materials_hint, list) and len(materials_hint) > 0:
        init = pd.DataFrame({"material": materials_hint, "mass_fraction": [0.0] * len(materials_hint)})
    else:
        init = pd.DataFrame({"material": ["cotton"], "mass_fraction": [1.0]})

    df = st.data_editor(
        init,
        num_rows="dynamic",
        use_container_width=True,
        key=f"{key_prefix}_bom_editor",
        column_config={
            "material": st.column_config.TextColumn("Material"),
            "mass_fraction": st.column_config.NumberColumn("Mass fraction", min_value=0.0, max_value=1.0, step=0.01),
        },
    )

    # Clean + validate
    df = df.copy()
    df["material"] = df["material"].astype(str).str.strip()
    df = df[df["material"] != ""]
    df["mass_fraction"] = pd.to_numeric(df["mass_fraction"], errors="coerce").fillna(0.0)

    total = float(df["mass_fraction"].sum()) if len(df) else 0.0
    tol = 1e-6

    if len(df) == 0:
        return df, False, "No materials provided."
    if (df["mass_fraction"] < 0).any():
        return df, False, "Mass fractions must be ≥ 0."
    if total <= 0:
        return df, False, "Sum of mass fractions must be > 0."
    if abs(total - 1.0) > tol:
        return df, False, f"Mass fractions must sum to 1.0 (current sum = {total:.6f})."

    return df, True, ""


# --------------------------- Run ---------------------------
if run_btn:
    dfp, dfc = _read_csvs(up_products, up_companies, products_path, companies_path)
    if dfp is None or dfc is None:
        st.error("Please provide both CSVs (upload or paths).")
    else:
        with st.spinner("Running C2G (hierarchy) then building G2G calculator…"):
            system, fp_db, calc, class_db, logs = run_c2g_then_g2g(
                dfp, dfc,
                year=year,
                region_id=region_id,
                verbose=verbose,
                max_iter=max_iter,
            )

        st.session_state["g2g_res"] = {
            "system": system,
            "fp_db": fp_db,
            "calc": calc,
            "class_db": class_db,
            "logs": logs,
            "year": int(year),
            "region_id": region_id,
        }

        # reset UI selections on a new run
        st.session_state["g2g_ui"] = {
            "selected_pid": None,
            "class_code": "",
            "primary_secondary": "primary",
        }

        st.success("Done.")

# --------------------------- Render ---------------------------
if "g2g_res" not in st.session_state:
    st.info("Upload CSVs and click **Run C2G + G2G**.")
    st.stop()

res = st.session_state["g2g_res"]
system = res["system"]
fp_db = res["fp_db"]
calc = res["calc"]
year = res["year"]
region_id = res["region_id"]

tabs = st.tabs(["1) C2G results", "2) G2G compute", "Logs"])

with tabs[0]:
    st.subheader("Cradle-to-gate (from hierarchy solver)")
    rows = []
    for pid, p, ent in _get_all_products(system):
        rows.append({
            "Entity": ent.name,
            "Product ID": pid,
            "Product name": getattr(p, "name", f"Product {pid}"),
            "C2G score": float(fp_db.get_footprint(int(pid))),
            "Unit": "kg CO2e / (product unit)",
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True)

with tabs[1]:
    st.subheader("Gate-to-grave extension (region-average)")

    all_products = _get_all_products(system)
    if not all_products:
        st.warning("No products found.")
        st.stop()

    # --- Step 1: select product FIRST ---
    st.markdown("### Step 1 — Select product")
    pid_options = [pid for pid, _, _ in all_products]

    # persistent selection
    ui = st.session_state.get("g2g_ui", {})
    default_pid = ui.get("selected_pid") if ui.get("selected_pid") in pid_options else pid_options[0]
    pid = st.selectbox("Product ID", pid_options, index=pid_options.index(default_pid))

    # retrieve chosen product + entity
    product_obj, ent_obj = None, None
    for _pid, p, ent in all_products:
        if _pid == int(pid):
            product_obj, ent_obj = p, ent
            break

    st.write(f"**Company:** {getattr(ent_obj, 'name', '(unknown)')}")
    st.write(f"**Product name:** {getattr(product_obj, 'name', f'Product {pid}')}")

    # --- Step 2: enter/confirm classification + metadata ---
    st.markdown("### Step 2 — Classification & metadata")
    inferred_class = _infer_class_code_from_product(product_obj)
    inferred_ps = _infer_primary_secondary_from_product(product_obj)

    col1, col2, col3 = st.columns([1.2, 1.2, 1.6])

    with col1:
        # if product has a class, use it; else fall back to last used; else blank
        cc_default = ui.get("class_code") or inferred_class or "6109"
        class_code = st.text_input("Classification code (class_code)", value=str(cc_default))

    with col2:
        ps_default = ui.get("primary_secondary") or inferred_ps
        primary_secondary = st.selectbox("Primary / Secondary", ["primary", "secondary"], index=0 if ps_default == "primary" else 1)

    with col3:
        # read-only function from ClassificationDatabase (now it actually makes sense)
        function_from_class = "(unknown — class_code not found)"
        function_unit_from_class = ""
        info = res["class_db"].get_class_info(str(class_code).strip())
        function_from_class = str(info[-2]) if len(info) >= 2 else "(unknown — class_code not found)"
        function_unit_from_class = str(info[-1]) if len(info) >= 3 else ""

        st.text_input("Function (from ClassificationDatabase)", value=function_from_class, disabled=True)
        if function_unit_from_class:
            st.caption(f"Function unit: **{function_unit_from_class}**")

    # persist selection state
    st.session_state["g2g_ui"] = {
        "selected_pid": int(pid),
        "class_code": str(class_code),
        "primary_secondary": primary_secondary,
    }

    # --- Step 3: resolve rule (only after product + class_code exist) ---
    st.markdown("### Step 3 — Resolve rule for this class_code")
    try:
        ruleset = calc.rule_catalog.resolve_ruleset(str(class_code))
    except Exception as e:
        st.error(f"Could not resolve a RuleSet for class_code='{class_code}': {e}")
        st.stop()

    st.success(f"Resolved RuleSet: **{ruleset.name}** (v{getattr(ruleset, 'version', '?')})")

    with st.expander("Resolved rule details (read-only)", expanded=False):
        st.write(f"**Rule name:** {ruleset.name}")
        st.write(f"**Rule version:** {getattr(ruleset, 'version', '')}")
        st.write(f"**Rule ID:** {getattr(ruleset, 'rule_set_id', '')}")
        if getattr(ruleset, "rule_params", None):
            st.markdown("**Rule parameters**")
            st.json(ruleset.rule_params)

        st.markdown("**Flows**")
        flow_rows = []
        for f in getattr(ruleset, "flows", []):
            flow_rows.append({
                "flow_id": getattr(f, "flow_id", ""),
                "producer_param_name": getattr(f, "producer_param_name", ""),
                "quantity_source": str(getattr(f, "quantity_source", "")),
                "default_quantity": getattr(f, "default_quantity", None),
                "unit": getattr(f, "quantity_unit", ""),
                "stage": str(getattr(getattr(f, "stage", None), "value", getattr(f, "stage", ""))),
                "market_key": ruleset.market_key_map.get(getattr(f, "flow_id", ""), ""),
            })
        st.dataframe(pd.DataFrame(flow_rows), use_container_width=True)

    # --- Step 4: producer inputs (driven by RuleSet) ---
    st.markdown("### Step 4 — Producer-specific inputs (requested by this rule)")
    producer_params = {}

    prod_flows = [
        f for f in ruleset.flows
        if getattr(f, "quantity_source", None) in (ParamSource.PRODUCER_REQUIRED, ParamSource.PRODUCER_OPTIONAL)
    ]

    # Detect whether the rule asks for a BOM/composition input (universal pattern)
    # We support multiple conventions without changing your backend:
    # - producer_param_name == "bom" or "composition" or "material_composition"
    # - OR quantity_unit indicates fractions AND param name suggests materials
    bom_param_names = {"bom", "composition", "material_composition", "material_mix"}
    bom_flow = None
    for f in prod_flows:
        pname = (getattr(f, "producer_param_name", "") or "").strip()
        u = (getattr(f, "quantity_unit", "") or "").lower()
        if pname in bom_param_names:
            bom_flow = f
            break
        if "fraction" in u and ("material" in pname or "bom" in pname or "composition" in pname):
            bom_flow = f
            break

    # Numeric producer inputs (excluding BOM-style, which we handle with a table UI)
    numeric_prod_flows = []
    for f in prod_flows:
        pname = (getattr(f, "producer_param_name", "") or "").strip()
        if bom_flow is not None and f is bom_flow:
            continue
        numeric_prod_flows.append(f)

    if not prod_flows:
        st.caption("This RuleSet does not request any producer-specific quantities.")
    else:
        # 4a) BOM editor if requested by rule
        bom_df = None
        if bom_flow is not None:
            # optional hint from rule_params if present
            materials_hint = None
            try:
                rp = getattr(ruleset, "rule_params", {}) or {}
                materials_hint = rp.get("bom_materials", None)  # e.g., ["cotton", "polyester"]
            except Exception:
                materials_hint = None

            bom_df, bom_ok, bom_err = _render_bom_editor(key_prefix=f"pid{pid}_cc{class_code}", materials_hint=materials_hint)
            if not bom_ok:
                st.error(bom_err)
            else:
                # store a clean dict
                bom_dict = {row["material"]: float(row["mass_fraction"]) for _, row in bom_df.iterrows()}
                producer_params[(getattr(bom_flow, "producer_param_name", None) or "bom")] = bom_dict
                st.success("Composition accepted (fractions sum to 1.0).")

        # 4b) numeric producer params
        if numeric_prod_flows:
            st.markdown("#### Quantities")
            for f in numeric_prod_flows:
                pname = getattr(f, "producer_param_name", None) or getattr(f, "flow_id", "param")
                default_val = float(getattr(f, "default_quantity", 0.0) or 0.0)
                req_txt = "required" if f.quantity_source == ParamSource.PRODUCER_REQUIRED else "optional"
                unit = getattr(f, "quantity_unit", "")

                # If we have a BOM and this is "mass_kg", keep it visible first (common)
                label = f"{pname} ({req_txt}, unit: {unit})"
                val = st.number_input(label, value=default_val, step=0.01)
                producer_params[str(pname)] = float(val)

        # Hard validation for required params
        missing = []
        for f in prod_flows:
            if getattr(f, "quantity_source", None) != ParamSource.PRODUCER_REQUIRED:
                continue
            pname = getattr(f, "producer_param_name", None) or getattr(f, "flow_id", None)
            if pname is None:
                continue
            if str(pname) not in producer_params and pname not in producer_params:
                # allow bom dict key (non-str) fallback
                missing.append(str(pname))

        if missing:
            st.error(f"Missing required producer inputs: {', '.join(missing)}")
            st.stop()

    # --- Step 5: compute (only now) ---
    st.markdown("### Step 5 — Compute")
    c2g_score = float(fp_db.get_footprint(int(pid)))
    st.write(f"**Cradle-to-gate footprint (C2G):** {c2g_score:.6f}")

    # Build a proper G2GProduct wrapper
    g2g_prod = G2GProduct(
        product_id=product_obj.product_id,
        name=getattr(product_obj, "name", f"Product {product_obj.product_id}"),
        unit=getattr(product_obj, "unit", "$"),
        company=getattr(product_obj, "company", None),
        class_code=str(class_code),
        g2g_inputs=producer_params,
    )
    setattr(g2g_prod, "primary_secondary", primary_secondary)
    setattr(g2g_prod, "function", function_from_class)

    compute_disabled = False
    if "bom_df" in locals() and bom_flow is not None:
        # if bom requested and invalid, we already st.stop(); keep as safety
        if bom_df is None or len(bom_df) == 0:
            compute_disabled = True

    if st.button("Compute G2G", type="primary", disabled=compute_disabled):
        total_score, fu_total = calc.compute_g2g_score(
            product=g2g_prod,
            fp_db=fp_db,
            year=year,
            region_id=region_id,
            producer_params=producer_params,
            store_result=False,
        )

        g2g_increment = float(total_score) - c2g_score

        st.markdown("### Results")
        st.write(f"**Cradle-to-gate footprint (C2G):** {c2g_score:.6f}")
        st.write(f"**Downstream contribution (retail + use + end-of-life):** {g2g_increment:.6f}")
        st.write(f"**Cradle-to-grave footprint (total):** {float(total_score):.6f}")

        if fu_total is not None and float(fu_total) > 0:
            unit = function_unit_from_class or "FU"
            st.write(f"**Total function delivered (from rule):** {float(fu_total):.3f} {unit}")
            st.write(f"**Footprint per functional unit:** {(float(total_score) / float(fu_total)):.9f} kg CO2e/{unit}")
        else:
            st.caption("No functional output total returned by this RuleSet (None).")

with tabs[2]:
    st.subheader("Logs")
    if verbose:
        st.code(res.get("logs", ""), language="text")
    else:
        st.caption("Verbose logging is off.")
