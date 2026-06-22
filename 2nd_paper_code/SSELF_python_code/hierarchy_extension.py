# hierarchy_extension.py

from SSELF_base import Company, Product, System
import pandas as pd
import numpy as np
import random
import time
import numbers


class HierarchicalCompany(Company):
    """
    Extends the basic Company to handle sub-companies.
    """
    def __init__(self, name, year, purchases=None, sales=None, direct_impacts=None):
        super().__init__(name, year, purchases, sales, direct_impacts)
        self.sub_companies = []
        self._prod_signature = {}  # product_id -> tuple used to detect change

    def _product_signature(self, footprint_db, product_id):
        """
        Compact, hashable snapshot of the inputs that determine a product's intensity.
        Returns a tuple (in_emb, in_direct, sales_value), rounded to avoid float jitter.
        """
        # Upstream scores
        df = footprint_db.data.copy()
        if not df.empty:
            df["id"] = df["id"].astype(int)
            scores = df.set_index("id")["scores"]
        else:
            scores = pd.Series(dtype=float)

        # Purchases aligned to scores
        pur = self.purchases if isinstance(self.purchases, pd.Series) else pd.Series(dtype=float)
        if not pur.empty:
            try:
                pur.index = pur.index.astype(int)
            except Exception:
                pur = pd.Series(dtype=float)

        common = pur.index.intersection(scores.index)
        in_emb = float(pur.loc[common] @ scores.loc[common]) if len(common) else 0.0

        # Direct impacts total
        in_direct = 0.0
        if isinstance(self.direct_impacts, pd.DataFrame) and not self.direct_impacts.empty:
            try:
                in_direct = float(self.direct_impacts.sum(numeric_only=True).sum())
            except Exception:
                in_direct = 0.0

        # Sales for this product
        s = 0.0
        if isinstance(self.sales, pd.DataFrame) and "Sales" in self.sales.columns and product_id in self.sales.index:
            try:
                s = float(self.sales.loc[product_id, "Sales"])
            except Exception:
                s = 0.0

        # Round to stabilize comparisons
        return (round(in_emb, 12), round(in_direct, 12), round(s, 12))

    def add_sub_company(self, sub_company):
        self.sub_companies.append(sub_company)

    def get_total_direct_impacts(self):
        total = self.direct_impacts.sum().sum()
        for sub in self.sub_companies:
            total += sub.get_total_direct_impacts() # GMB - suggestions
        return total

    def update_footprint(self, footprint_db):
        # 1) Update children first
        for sub in self.sub_companies:
            sub.update_footprint(footprint_db)
            sub.report_footprint(footprint_db)

        print(f"\nCalculating footprint for {self.name}")

        # 2) Prepare inputs safely
        # Purchases: ensure Series[int->float], may be empty
        if isinstance(self.purchases, pd.Series) and not self.purchases.empty:
            try:
                pid_index = self.purchases.index.astype(int)
                purchases = self.purchases.copy()
                purchases.index = pid_index
            except Exception:
                # If index cannot be cast to int, treat as no valid purchases
                purchases = pd.Series(dtype=float)
        else:
            purchases = pd.Series(dtype=float)

        # Footprint DB ids/scores
        if "id" in footprint_db.data.columns:
            footprint_db.data["id"] = footprint_db.data["id"].astype(int)
        scores = footprint_db.data.set_index("id")["scores"] if not footprint_db.data.empty else pd.Series(dtype=float)

        # 3) Compute total impacts (skip missing ids gracefully)
        valid_ids = [pid for pid in purchases.index if pid in scores.index]
        missing_ids = [pid for pid in purchases.index if pid not in scores.index]
        if missing_ids:
            print(f"  WARNING: Missing product IDs in footprint DB: {missing_ids}")

        total_impacts = float(scores.loc[valid_ids] @ purchases.loc[valid_ids]) if valid_ids else 0.0

        # Direct impacts (optional/empty-safe)
        if isinstance(self.direct_impacts, pd.DataFrame) and not self.direct_impacts.empty:
            try:
                total_impacts += float(self.direct_impacts.sum(numeric_only=True).sum())
            except Exception:
                pass  # ignore if weird types slip in

        # 4) Assign product footprints using company-wide intensity (ensures carbon balance)
        sales_df = self.sales if isinstance(self.sales, pd.DataFrame) else pd.DataFrame()
        if not sales_df.empty and "Sales" not in sales_df.columns:
            # if sales provided as a single unnamed column, rename defensively
            if sales_df.shape[1] == 1:
                sales_df = sales_df.rename(columns={sales_df.columns[0]: "Sales"})
                self.sales = sales_df

        # Gather sales only for this company's own products
        own_products = list(getattr(self, "products", []))
        own_sales_vals = []
        for p in own_products:
            sv = 0.0
            if not sales_df.empty and "Sales" in sales_df.columns and p.product_id in sales_df.index:
                try:
                    sv = float(sales_df.loc[p.product_id, "Sales"])
                except Exception:
                    sv = 0.0
            own_sales_vals.append(sv)

        total_sales_this_company = float(sum(own_sales_vals))

        if total_sales_this_company > 0.0:
            # Single intensity applied to all own products → exact carbon balance
            intensity = total_impacts / total_sales_this_company
            for p in own_products:
                print(f"  Product {p.name}: Assigned footprint {intensity:.4f}")
                p.footprint = float(intensity)
            self.latest_update = float(intensity)
            print(f"  Final footprint for {self.name}: {self.latest_update:.4f}")
        else:
            # No sales → zero intensity by convention
            for p in own_products:
                p.footprint = 0.0
            self.latest_update = 0.0

        # 5) Set latest_update (never leave as None)
        if getattr(self, "products", []):
            # sum only over products that exist in sales index
            sales_vals = []
            weighted = []
            for p in self.products:
                if not sales_df.empty and "Sales" in sales_df.columns and p.product_id in sales_df.index:
                    sv = float(sales_df.loc[p.product_id, "Sales"])
                    sales_vals.append(sv)
                    weighted.append(p.footprint * sv)
            total_sales = sum(sales_vals)
            if total_sales > 0:
                self.latest_update = float(sum(weighted) / total_sales)
                print(f"  Final footprint for {self.name}: {self.latest_update:.4f}")
            else:
                self.latest_update = 0.0
        else:
            # No products to allocate → neutral default
            self.latest_update = 0.0

    def check_update_needed(self, footprint_db, atol=1e-6):
        # 1) Children first (but do not early-return)
        children_changed = False
        for sub in self.sub_companies:
            if sub.check_update_needed(footprint_db, atol=atol):
                children_changed = True

        # 2) Did any product's inputs change?
        inputs_changed = False
        for p in getattr(self, "products", []):
            sig = self._product_signature(footprint_db, p.product_id)
            if self._prod_signature.get(p.product_id) != sig:
                inputs_changed = True
                break

        self_changed = False
        previous = self.latest_update

        if inputs_changed:
            # Recompute intensity and set product footprints
            self.update_footprint(footprint_db)
            new = self.latest_update
            self_changed = (previous is None) or (not np.isclose(previous, new, atol=atol))

            # Cache new signatures for ALL products
            for p in getattr(self, "products", []):
                self._prod_signature[p.product_id] = self._product_signature(footprint_db, p.product_id)

            if self_changed:
                self.num_updates += 1
                self.report_footprint(footprint_db)

        return children_changed or self_changed

    def carbon_balance(self, fp_db, atol=1e-6, verbose=False):
        """
        Returns a dict with in_emb, in_direct, in_total, out_total, delta, balanced.
        """
        # Scores as a Series indexed by product id
        scores = pd.Series(dtype=float)
        if not fp_db.data.empty:
            df = fp_db.data.copy()
            df["id"] = df["id"].astype(int)
            scores = df.set_index("id")["scores"]

        # Purchases aligned to scores
        in_emb = 0.0
        if isinstance(self.purchases, pd.Series) and not self.purchases.empty and not scores.empty:
            try:
                pidx = self.purchases.index.astype(int)
                pur = self.purchases.copy()
                pur.index = pidx
                common = pur.index.intersection(scores.index)
                in_emb = float(pur.loc[common] @ scores.loc[common])
            except Exception:
                in_emb = 0.0

        # Direct impacts
        in_direct = 0.0
        if isinstance(self.direct_impacts, pd.DataFrame) and not self.direct_impacts.empty:
            try:
                in_direct = float(self.direct_impacts.sum(numeric_only=True).sum())
            except Exception:
                in_direct = 0.0

        in_total = in_emb + in_direct

        # Outputs = sum(footprint * sales)
        out_total = 0.0
        if isinstance(self.sales, pd.DataFrame) and "Sales" in self.sales.columns:
            for p in getattr(self, "products", []):
                if p.product_id in self.sales.index:
                    try:
                        s = float(self.sales.loc[p.product_id, "Sales"])
                    except Exception:
                        s = 0.0
                    out_total += float(getattr(p, "footprint", 0.0)) * s

        delta = out_total - in_total
        balanced = np.isclose(out_total, in_total, atol=atol)

        if verbose:
            print(f"[Balance] {self.name}: in_emb={in_emb:.6f}, in_direct={in_direct:.6f}, "
                  f"in_total={in_total:.6f}, out_total={out_total:.6f}, "
                  f"Δ={delta:.6f}, ok={balanced}")

        return {
            "entity": self.name,
            "in_emb": in_emb,
            "in_direct": in_direct,
            "in_total": in_total,
            "out_total": out_total,
            "delta": delta,
            "balanced": bool(balanced),
        }

class HierarchicalSystem(System):
    """
    Extends the basic System to support hierarchical sub-entities.
    """
    def __init__(self, num_companies=None, num_products=None):
        if num_companies is not None and num_products is not None:
            super().__init__(num_companies, num_products)
            self.product_id_counter = num_products + 1
        else:
            self.companies = {}
            self.product_id_counter = 1
            self.num_companies = 0
            self.num_products = 0

    def create_companies(self):
        for i in range(self.num_companies):
            purchases = pd.Series(self.use_data[:, i], index=[j + 1 for j in range(self.num_products)])
            sales = {}
            direct_impacts = pd.DataFrame({"kg CO2eq": [np.random.randint(0, 659)]})

            company = HierarchicalCompany(f'Company {i + 1}', 2024, purchases, sales, direct_impacts)
            product_id = i + 1
            sales[product_id] = self.use_data[:, i].sum() * self.margins_data[i]

            product = Product(product_id, f"Product {product_id}", "unit", company)
            company.add_product(product)
            company.sales = pd.DataFrame.from_dict(sales, orient="index", columns=["Sales"])

            self.companies[f'Company_{i + 1}'] = company

    def add_hierarchies(self, target_company_names, num_subs_per_hierarchical, starting_id):
        current_id = starting_id
        for name in target_company_names:
            company = self.companies.get(name)
            if not company:
                continue
            for s in range(num_subs_per_hierarchical):
                sub_name = f"{company.name}_Sub_{s+1}"
                sub_purchases = pd.Series({
                    np.random.randint(1, self.num_products): np.random.uniform(0.5, 2.0)
                    for _ in range(3)
                })
                sub_sales = pd.DataFrame.from_dict({
                    self.product_id_counter: np.random.uniform(50, 150)
                }, orient="index", columns=["Sales"])

                sub = HierarchicalCompany(sub_name, 2024, sub_purchases, sub_sales)
                product = Product(self.product_id_counter, f"Product {self.product_id_counter}", "unit", sub)
                sub.add_product(product)

                company.add_sub_company(sub)
                self.product_id_counter += 1

        # ---------- Helpers over the hierarchy ----------
        # ---------- Build from CSV (string IDs ok) ----------

    def build_from_csv(self, companies_path, products_path):
        df_companies = pd.read_csv(companies_path, dtype=str).fillna("")
        df_products = pd.read_csv(products_path, dtype=str).fillna("")
        return self._build_from_frames(df_companies, df_products)

    def _build_from_frames(self, df_companies, df_products):
        required_company_cols = {"company_id", "parent_id", "name"}
        required_product_cols = {"product_id", "company_id", "name"}
        mc = required_company_cols - set(df_companies.columns)
        mp = required_product_cols - set(df_products.columns)
        if mc:
            raise ValueError(f"companies dataframe missing columns: {mc}")
        if mp:
            raise ValueError(f"products dataframe missing columns: {mp}")

        # normalize
        for col in ["company_id", "parent_id", "name"]:
            df_companies[col] = df_companies[col].astype(str).str.strip()
        for col in ["product_id", "company_id", "name"]:
            df_products[col] = df_products[col].astype(str).str.strip()

        # create companies keyed by string id
        company_objs = {}
        for _, row in df_companies.iterrows():
            cid = row["company_id"]
            name = row["name"]
            if not cid:
                raise ValueError("Empty company_id encountered.")
            company_objs[cid] = HierarchicalCompany(name, 2024)

        # link parents (string ids)
        for _, row in df_companies.iterrows():
            cid = row["company_id"]
            parent_id = row["parent_id"]
            if parent_id and parent_id in company_objs:
                company_objs[parent_id].add_sub_company(company_objs[cid])

        # products: map external codes to internal int ids
        code_to_internal = {}
        next_internal = getattr(self, "product_id_counter", 1)

        for _, row in df_products.iterrows():
            pcode = row["product_id"]  # e.g., 'P1'
            ccid = row["company_id"]  # e.g., 'C1.1.1'
            pname = row["name"]
            if ccid not in company_objs:
                raise ValueError(f"Product '{pname}' references unknown company_id '{ccid}'.")

            if pcode not in code_to_internal:
                code_to_internal[pcode] = next_internal
                next_internal += 1

            pid_internal = code_to_internal[pcode]
            product = Product(pid_internal, pname, "unit", company_objs[ccid])
            company_objs[ccid].add_product(product)

        # top-level companies are those not present as any sub
        all_subs = {sub for c in company_objs.values() for sub in getattr(c, "sub_companies", [])}
        top_level = {cid: c for cid, c in company_objs.items() if c not in all_subs}

        self.companies = top_level
        self.product_id_counter = next_internal
        self.num_companies = len(self.companies)
        self.num_products = len(df_products)

        # optional mappings
        self._product_code_to_id = code_to_internal
        self._product_id_to_code = {v: k for k, v in code_to_internal.items()}
        return self

    def carbon_balance_all(self, fp_db, atol=1e-6, verbose=False):
        """Run carbon balance for every entity (top + all descendants)."""
        rows = []
        for ent in self._iter_all_entities():
            rows.append(ent.carbon_balance(fp_db, atol=atol, verbose=verbose))
        return pd.DataFrame(rows)

    def carbon_balance_summary(self, fp_db, atol=1e-6):
        """System-level aggregates (note: sums double-count internal flows by design)."""
        df = self.carbon_balance_all(fp_db, atol=atol, verbose=False)
        return {
            "sum_in_total": float(df["in_total"].sum()),
            "sum_out_total": float(df["out_total"].sum()),
            "sum_delta": float(df["delta"].sum()),
            "all_balanced": bool((df["balanced"]).all()),
        }

    def _iter_all_entities(self):
        """Yield every HierarchicalCompany in the system (top-level + descendants)."""

        def dfs(c):
            yield c
            for s in getattr(c, "sub_companies", []):
                yield from dfs(s)

        for c in self.companies.values():
            yield from dfs(c)

    def _collect_all_products(self):
        """Return {entity: [Product,...]}, and a flat list of all products."""
        company_to_products = {}
        all_products = []
        for ent in self._iter_all_entities():
            plist = list(getattr(ent, "products", []))
            company_to_products[ent] = plist
            all_products.extend(plist)
        return company_to_products, all_products

    def _descendants(self, ent):
        for s in getattr(ent, "sub_companies", []):
            yield s
            yield from self._descendants(s)

    def _entity_total_sales(self, ent) -> float:
        if isinstance(ent.sales, pd.DataFrame) and "Sales" in ent.sales.columns:
            return float(ent.sales["Sales"].sum())
        return 0.0

    def _trickle_down_parent_costs_dynamic(self):
        """
        Per-iteration allocation:
          1) Reset all entities to baseline purchases/direct_impacts.
          2) For parents with NO OWN PRODUCTS, distribute their baseline costs to
             descendant sellers proportional to descendant total sales.
        This is idempotent per iteration and safe to call repeatedly.
        """
        # Build the universe of product IDs from baselines to keep indices aligned
        all_pids = sorted({
            int(pid)
            for e in self._iter_all_entities()
            if isinstance(getattr(e, "_base_purchases", None), pd.Series)
            for pid in e._base_purchases.index
        })
        pid_index = pd.Index(all_pids, dtype=int)

        # 1) reset everyone to baseline
        for ent in self._iter_all_entities():
            # reset purchases
            if isinstance(getattr(ent, "_base_purchases", None), pd.Series):
                p = ent._base_purchases.copy()
                p.index = p.index.astype(int)
                ent.purchases = p.reindex(pid_index, fill_value=0.0)
            else:
                ent.purchases = pd.Series(0.0, index=pid_index)

            # reset direct_impacts
            if isinstance(getattr(ent, "_base_direct_impacts", None), pd.DataFrame):
                ent.direct_impacts = ent._base_direct_impacts.copy()
            else:
                ent.direct_impacts = pd.DataFrame({"kg CO2eq": [0.0]})

        # helper to ensure child has aligned purchases
        def ensure_purchases(e):
            if not isinstance(e.purchases, pd.Series):
                e.purchases = pd.Series(0.0, index=pid_index)
            else:
                if not e.purchases.index.equals(pid_index):
                    try:
                        e.purchases.index = e.purchases.index.astype(int)
                    except Exception:
                        e.purchases = pd.Series(0.0, index=pid_index)
                    e.purchases = e.purchases.reindex(pid_index, fill_value=0.0)

        # 2) push down from parents with NO OWN PRODUCTS
        for parent in self._iter_all_entities():
            if getattr(parent, "products", []):
                continue  # only allocate from parents that have NO own products

            # targets are descendant sellers
            targets = [d for d in self._descendants(parent) if getattr(d, "products", [])]
            if not targets:
                continue

            # weights by descendant total sales (fallback equal)
            weights = np.array([max(self._entity_total_sales(d), 0.0) for d in targets], dtype=float)
            if weights.sum() == 0.0:
                weights = np.ones(len(targets), dtype=float)
            shares = weights / weights.sum()

            # parent baseline purchases to distribute
            parent_pur = getattr(parent, "_base_purchases", pd.Series(0.0, index=pid_index))
            if isinstance(parent_pur, pd.Series):
                parent_pur = parent_pur.copy()
                try:
                    parent_pur.index = parent_pur.index.astype(int)
                except Exception:
                    parent_pur = pd.Series(0.0, index=pid_index)
                parent_pur = parent_pur.reindex(pid_index, fill_value=0.0)
            else:
                parent_pur = pd.Series(0.0, index=pid_index)

            if parent_pur.sum() != 0.0:
                for d, w in zip(targets, shares):
                    ensure_purchases(d)
                    # mask out the child's own product IDs to avoid self-consumption
                    own_pids = [p.product_id for p in getattr(d, "products", [])]
                    if own_pids:
                        add_vec = parent_pur.copy()
                        # parent_pur is on pid_index; safe to loc-mask
                        add_vec.loc[own_pids] = 0.0
                    else:
                        add_vec = parent_pur
                    d.purchases = d.purchases.add(add_vec * float(w), fill_value=0.0)

            # parent baseline direct impacts to distribute
            di_total = 0.0
            if isinstance(getattr(parent, "_base_direct_impacts", None),
                          pd.DataFrame) and not parent._base_direct_impacts.empty:
                try:
                    di_total = float(parent._base_direct_impacts.sum(numeric_only=True).sum())
                except Exception:
                    di_total = 0.0

            if di_total > 0.0:
                for d, w in zip(targets, shares):
                    add = di_total * float(w)
                    if not isinstance(d.direct_impacts, pd.DataFrame) or d.direct_impacts.empty:
                        d.direct_impacts = pd.DataFrame({"kg CO2eq": [add]})
                    else:
                        d.direct_impacts.iloc[0, 0] = float(d.direct_impacts.iloc[0, 0]) + add

            # CONSERVATION: remove from parent what we pushed to children
            if parent_pur.sum() != 0.0:
                parent.purchases = pd.Series(0.0, index=pid_index)

            if di_total > 0.0:
                parent.direct_impacts = pd.DataFrame({"kg CO2eq": [0.0]})

        # ---------- Initialize purchases/sales/direct_impacts for all entities ----------

    def _initialize_accounting(self, margin_low=1.28, margin_high=1.89, random_state=None, avoid_self_consumption=True):
        rng = np.random.default_rng(random_state)
        if random_state is not None:
            random.seed(random_state)

        company_to_products, all_products = self._collect_all_products()
        if not all_products:
            raise ValueError("No products found in the system. Build products before initializing accounting.")

        all_pids = sorted({int(p.product_id) for p in all_products})

        self._entity_margins = {}
        for ent in self._iter_all_entities():
            # direct impacts
            ent.direct_impacts = pd.DataFrame({"kg CO2eq": [float(rng.integers(0, 659))]})

            # margin per entity
            margin = random.uniform(margin_low, margin_high)
            self._entity_margins[ent] = margin

            # purchases across all products
            base = rng.uniform(0.5, 2.0, size=len(all_pids))
            purchases = pd.Series(base, index=all_pids, dtype=float)

            if avoid_self_consumption and company_to_products[ent]:
                own_pids = [p.product_id for p in company_to_products[ent]]
                purchases.loc[own_pids] = 0.0

            ent.purchases = purchases

            # total sales value
            total_sales_val = float(ent.purchases.sum()) * margin

            # split sales across this entity's own products (even split)
            own_products = company_to_products[ent]
            if own_products:
                per_prod = total_sales_val / len(own_products)
                sales_map = {p.product_id: per_prod for p in own_products}
            else:
                sales_map = {}

            ent.sales = pd.DataFrame.from_dict(sales_map, orient="index", columns=["Sales"])

            # === BASELINE SNAPSHOTS (used to make trickle idempotent) ===
            ent._base_purchases = ent.purchases.copy()
            ent._base_direct_impacts = ent.direct_impacts.copy()

        # ---------- Solve loop (random by default; reproducible if random_state provided) ----------

    def solve(
            self,
            footprint_db,
            forced_updates=0,
            verbose=True,
            random_state=None,
            max_iter=5000,
            progress_callback=None,  # <--- NEW
    ):
        # Helper: call progress_callback safely
        def safe_call(cb, it, sys_obj):
            if cb is None:
                return
            try:
                cb(it, sys_obj)
            except Exception as _e:
                # don't crash the solver because of UI/collection issues
                if verbose:
                    print(f"[progress_callback error at iter {it}]: {_e}")

        # Step 0: initialize accounting (sets _entity_margins, baselines, etc.)
        self._initialize_accounting(random_state=random_state)

        start_time = time.time()
        updates_completed = False
        iteration = 0

        # Snapshot initial state (iteration 0)
        safe_call(progress_callback, iteration, self)

        while not updates_completed and iteration < max_iter:
            iteration += 1
            if verbose:
                print(f"\n--- Iteration {iteration} ---")

            # 1) Recompute allocation from baselines each iteration
            self._trickle_down_parent_costs_dynamic()

            # 2) Recompute sales from (current) purchases so denominators match this iteration
            company_to_products, _ = self._collect_all_products()
            for ent in self._iter_all_entities():
                own_products = company_to_products.get(ent, [])
                total_sales_val = float(ent.purchases.sum()) * self._entity_margins.get(ent, 1.0)
                if own_products:
                    per_prod = total_sales_val / len(own_products) if len(own_products) else 0.0
                    sales_map = {p.product_id: per_prod for p in own_products}
                    ent.sales = pd.DataFrame.from_dict(sales_map, orient="index", columns=["Sales"])
                else:
                    # entities with no own products should not carry sales
                    ent.sales = pd.DataFrame(columns=["Sales"])

            # 3) Update loop
            any_company_updated = False
            companies_to_check = list(self.companies.keys())
            random.shuffle(companies_to_check)

            for cname in companies_to_check:
                company = self.companies[cname]
                if verbose:
                    print(f"Checking {cname}")
                if company.check_update_needed(footprint_db):
                    if verbose:
                        val = company.latest_update
                        if isinstance(val, numbers.Real):
                            print(f"  → Updated. New: {val:.4f}")
                        else:
                            print("  → Updated. New: N/A")
                    any_company_updated = True
                elif verbose:
                    print("  → No update needed.")

            updates_completed = not any_company_updated

            # Snapshot end-of-iteration state
            safe_call(progress_callback, iteration, self)

        if iteration >= max_iter and verbose:
            print(f"\n⚠️ Reached max_iter={max_iter} without full convergence.")

        end_time = time.time()
        print("\n✅ Updates completed.")
        print(f"⏱️ Time taken: {end_time - start_time:.2f} seconds")

        print("\n📦 Final Footprints:")
        for cname, company in self.companies.items():
            val = company.latest_update
            if isinstance(val, numbers.Real):
                print(f"{cname}: {val:.4f} kg CO2e/unit")
            else:
                print(f"{cname}: N/A")

        print("\n🔁 Update Count:")
        total_updates = sum(c.num_updates for c in self.companies.values())
        for cname, c in self.companies.items():
            print(f"{cname}: {c.num_updates} updates")
        print(f"Total updates: {total_updates}")

        # Optional extra passes, then final snapshot
        for i in range(forced_updates):
            print(f"\n--- Forced Update Iteration {i + 1} ---")
            for cname, company in self.companies.items():
                company.update_footprint(footprint_db)
                company.report_footprint(footprint_db)

        print("\n🗃️ Final footprint database:")
        print(footprint_db.data.sort_values("id"))

        # Final state snapshot (post-forced updates)
        safe_call(progress_callback, iteration, self)
