# substitution_extension.py

import pandas as pd
import numpy as np
import time
import random
from SSELF_base import FootprintDatabase
from SSELF_base import Product as BaseProduct
from SSELF_base import Company as BaseCompany
from SSELF_base import System as BaseSystem


class Product(BaseProduct):
    def __init__(self, product_id, name, unit, company, class_code, primary_secondary, function_output, classification_db):
        super().__init__(product_id, name, unit, company)
        self.class_code = class_code
        self.primary_secondary = primary_secondary
        self.function_output = function_output
        self.classification_db = classification_db


class Company(BaseCompany):
    def __init__(self, name, year, purchases=None, sales=None, direct_impacts=None):
        super().__init__(name, year, purchases, sales, direct_impacts)
        self.num_updates = 0

    def update_footprint(self, fp_db_2024, fp_db_2023, last_year_sales_db):
        df24 = fp_db_2024.data.copy()
        if not df24.empty:
            df24["id"] = df24["id"].astype(int)
        scores = df24.set_index("id")["scores"] if not df24.empty else pd.Series(dtype=float)

        pur = self.purchases if isinstance(self.purchases, pd.Series) else pd.Series(dtype=float)
        if not pur.empty:
            try:
                pur.index = pur.index.astype(int)
            except Exception:
                pur = pd.Series(dtype=float)

        common = pur.index.intersection(scores.index)
        in_emb = float(pur.loc[common] @ scores.loc[common]) if len(common) else 0.0
        in_dir = 0.0
        if isinstance(self.direct_impacts, pd.DataFrame) and not self.direct_impacts.empty:
            try:
                in_dir = float(self.direct_impacts.sum(numeric_only=True).sum())
            except Exception:
                in_dir = 0.0
        total_impacts = in_emb + in_dir

        primary_impacts = {}
        for product in self.products:
            sales_vol = self.sales.loc[product.product_id, "Sales"] if product.product_id in self.sales.index else 0
            if product.primary_secondary == "primary":
                primary_impacts[product.product_id] = total_impacts
            else:
                avg = self.get_average_footprint(product.class_code, last_year_sales_db, fp_db_2023)
                sub_impact = avg * sales_vol * product.function_output
                for p in self.products:
                    if p.primary_secondary == "primary":
                        primary_impacts[p.product_id] -= sub_impact
                product.footprint = sub_impact / sales_vol if sales_vol else 0

        for product in self.products:
            if product.primary_secondary == "primary":
                vol = self.sales.loc[product.product_id, "Sales"] if product.product_id in self.sales.index else 0
                product.footprint = primary_impacts[product.product_id] / vol if vol else 0

        self.latest_update = sum(p.footprint * p.function_output for p in self.products) / max(
            sum(p.function_output for p in self.products), 1)
        self.num_updates += 1
        self.report_footprint(fp_db_2024)

    def get_average_footprint(self, class_code, sales_db, fp_db):
        """
        Compute the market-average displaced intensity for the function
        associated with class_code.

        Unit logic:
        - sales_volume is interpreted as annual sales value, e.g. dollars.
        - scores are historical footprint intensities, e.g. kg CO2e/$.
        - function_output is reference units of function per dollar.
        - returned value is kg CO2e per reference unit of function.
        """
        data = sales_db.get_sales_data_by_function(class_code)

        if data.empty:
            return 0.0

        fp_data = fp_db.data.copy()
        fp_data["id"] = fp_data["id"].astype(int)

        data = data.copy()
        data["id"] = pd.to_numeric(data["id"], errors="coerce").fillna(0).astype(int)
        data["sales_volume"] = pd.to_numeric(data["sales_volume"], errors="coerce").fillna(0.0)
        data["function_output"] = pd.to_numeric(data["function_output"], errors="coerce").fillna(0.0)

        merged = data.merge(fp_data, on="id", how="left")
        merged["scores"] = pd.to_numeric(merged["scores"], errors="coerce").fillna(0.0)

        total_impact = 0.0
        total_function = 0.0

        for _, row in merged.iterrows():
            sales_value = float(row["sales_volume"])
            footprint_per_dollar = float(row["scores"])
            function_per_dollar = float(row["function_output"])

            total_impact += sales_value * footprint_per_dollar
            total_function += sales_value * function_per_dollar

        return total_impact / total_function if total_function > 0 else 0.0

    def check_update_needed(self, fp_db_2024, last_year_sales_db, fp_db_2023):
        """
        Decide if any product footprint would change given current inputs.
        Mirrors the safe casting/alignment used in update_footprint(...).
        """
        # ---- Safe scores (2024) ----
        df24 = fp_db_2024.data.copy()
        if not df24.empty:
            df24["id"] = df24["id"].astype(int)
        scores24 = df24.set_index("id")["scores"] if not df24.empty else pd.Series(dtype=float)

        # ---- Safe purchases ----
        pur = self.purchases if isinstance(self.purchases, pd.Series) else pd.Series(dtype=float)
        if not pur.empty:
            try:
                pur.index = pur.index.astype(int)
            except Exception:
                pur = pd.Series(dtype=float)

        # overlap
        common = pur.index.intersection(scores24.index)
        in_emb = float(pur.loc[common] @ scores24.loc[common]) if len(common) else 0.0

        # ---- Safe direct impacts ----
        in_dir = 0.0
        if isinstance(self.direct_impacts, pd.DataFrame) and not self.direct_impacts.empty:
            try:
                in_dir = float(self.direct_impacts.sum(numeric_only=True).sum())
            except Exception:
                in_dir = 0.0

        total = in_emb + in_dir

        # ---- Helper: robust sales lookup ----
        def sales_of(pid: int) -> float:
            if not isinstance(self.sales, pd.DataFrame) or "Sales" not in self.sales.columns:
                return 0.0
            if pid not in self.sales.index:
                return 0.0
            try:
                return float(self.sales.loc[pid, "Sales"])
            except Exception:
                return 0.0

        # ---- Primary impacts bucket (like update_footprint) ----
        primary_impacts = {}
        for product in self.products:
            s_vol = sales_of(product.product_id)
            if product.primary_secondary == "primary":
                primary_impacts[product.product_id] = float(total)
            else:
                # Secondary: compute substitution credit using last-year sales + fp_db_2023
                avg = self.get_average_footprint(product.class_code, last_year_sales_db, fp_db_2023)
                sub_impact = float(avg) * float(s_vol) * float(product.function_output)
                # subtract from primaries
                for p in self.products:
                    if p.primary_secondary == "primary":
                        primary_impacts[p.product_id] -= sub_impact

        # ---- Compare simulated vs current per product ----
        for product in self.products:
            if product.primary_secondary == "primary":
                vol = sales_of(product.product_id)
                new_fp = (primary_impacts.get(product.product_id, 0.0) / vol) if vol > 0 else 0.0
            else:
                # For secondaries, intensity based on average * function_output.
                # (Volume only used to short-circuit zero-sales case, matching your original logic)
                vol = sales_of(product.product_id)
                avg = self.get_average_footprint(product.class_code, last_year_sales_db, fp_db_2023)
                new_fp = float(avg) * float(product.function_output) if vol > 0 else 0.0

            current_fp = float(getattr(product, "footprint", 0.0) or 0.0)

            print(f"\n[{self.name}] Checking product {product.product_id} ({product.name})")
            print(f"  Current footprint: {current_fp:.6f}, Simulated footprint: {new_fp:.6f}")
            print(f"  Difference: {abs(new_fp - current_fp):.6f}")

            if not np.isclose(new_fp, current_fp, atol=1e-6):
                return True

        return False

    def check_carbon_balance(self, fp_db):
        scores = fp_db.data.set_index("id")["scores"]
        valid_ids = [i for i in self.purchases.index if i in scores.index]
        in_emb = float(scores.loc[valid_ids] @ self.purchases.loc[valid_ids])
        in_direct = float(self.direct_impacts.sum().sum())
        carbon_in = in_emb + in_direct

        print(f"\n--- Carbon balance check for {self.name} ---")
        print(f"  Embodied in purchases: {in_emb:.4f}")
        print(f"  Direct emissions:      {in_direct:.4f}")
        print(f"  Total carbon in:       {carbon_in:.4f}")

        total_out = 0
        for product in self.products:
            sales = self.sales.loc[product.product_id, "Sales"] if product.product_id in self.sales.index else 0
            out = product.footprint * sales
            total_out += out
            print(f"  Product {product.name}: {out:.4f} kg CO2e in outputs")

        balanced = np.isclose(carbon_in, total_out, atol=1e-2)
        print(f"  Total embodied out:    {total_out:.4f}")
        print(f"  Balanced?              {balanced}")


class ClassificationDatabase:
    def __init__(self, data):
        self.data = pd.DataFrame(data)

    def get_sales_data(self, class_code):
        return self.data[self.data["class_code"] == class_code]

    def get_class_info(self, class_code):
        row = self.data[self.data["class_code"] == class_code]
        if not row.empty:
            return row.iloc[0]["class_name"], row.iloc[0]["function"], row.iloc[0]["unit"]
        return None, None, None


class SalesDatabase:
    def __init__(self, year, classification_db):
        self.year = year
        self.classification_db = classification_db
        self.data = pd.DataFrame(columns=["id", "class_code", "sales_volume", "unit", "function_output"])

    def add_sales(self, product, sales_volume):
        info = self.classification_db.get_class_info(product.class_code)
        if info[2] != product.unit:
            raise ValueError(f"Unit mismatch: {product.unit} vs {info[2]}")
        self.data = pd.concat([
            self.data,
            pd.DataFrame.from_records([{
                "id": product.product_id,
                "class_code": product.class_code,
                "sales_volume": sales_volume,
                "unit": product.unit,
                "function_output": product.function_output
            }])
        ], ignore_index=True)

    def get_sales_data_by_class_code(self, class_code):
        return self.data[self.data["class_code"] == class_code]

    def get_sales_data_by_function(self, class_code):
        """
        Return last-year sales records for all product classes that provide
        the same governed function, expressed in the same reference unit,
        as the selected class_code.
        """
        _, target_function, target_unit = self.classification_db.get_class_info(class_code)

        if target_function is None or target_unit is None:
            return self.data.iloc[0:0].copy()

        class_info = self.classification_db.data.copy()
        class_info["class_code"] = class_info["class_code"].astype(str).str.strip()
        class_info["function"] = class_info["function"].astype(str).str.strip()
        class_info["unit"] = class_info["unit"].astype(str).str.strip()

        matching_codes = class_info[
            (class_info["function"] == str(target_function).strip())
            & (class_info["unit"] == str(target_unit).strip())
            ]["class_code"].unique()

        data = self.data.copy()
        data["class_code"] = data["class_code"].astype(str).str.strip()

        return data[data["class_code"].isin(matching_codes)]


class System(BaseSystem):
    def __init__(self, num_companies, num_products, classification_db):
        self.classification_db = classification_db  #  Define this first
        super().__init__(num_companies, num_products)  # Then call base init

        self.reassign_products()

    def create_companies(self):
        self.companies = {}
        for i in range(self.num_companies):
            purchases = pd.Series(self.use_data[:, i], index=[j + 1 for j in range(self.num_products)])
            sales = {}
            direct_impacts = pd.DataFrame({"kg CO2eq": [np.random.randint(0, 659)]})

            company = Company(f"Company_{i + 1}", 2024, purchases, sales, direct_impacts)
            product_id = i + 1
            sales[product_id] = self.use_data[:, i].sum() * self.margins_data[i]

            product = Product(product_id, f"Product {product_id}", "unit", company, "class_code", "primary",
                              sales[product_id], self.classification_db)
            company.add_product(product)
            company.sales = pd.DataFrame.from_dict(sales, orient="index", columns=["Sales"])
            self.companies[f"Company_{i + 1}"] = company

    def reassign_products(self):
        for i, (name, company) in enumerate(self.companies.items()):
            pid = i + 1
            sales_val = company.sales.loc[pid, "Sales"]
            new_product = Product(
                pid,
                f"Product {pid}",
                "unit",
                company,
                "class_code",
                "primary",
                sales_val,
                self.classification_db
            )
            company.products = [new_product]
            company.sales = pd.DataFrame.from_dict({pid: sales_val}, orient="index", columns=["Sales"])


    def _ordered_company_names(self, schedule="random", rng=None):
        names = list(self.companies.keys())
        if schedule == "random":
            (rng or random).shuffle(names)
        # "cyclic" returns the insertion order
        return names

    def solve(
            self,
            fp_2024,
            fp_2023,
            last_year_sales,
            atol=1e-6,
            max_iter=200,
            seed=None,
            verbose=True,
            progress_callback=None,
    ):
        def _tick(it):
            if progress_callback is None:
                return
            try:
                setattr(self, "current_iteration", int(it))
                progress_callback(int(it), self)
            except Exception as _e:
                if verbose:
                    print(f"[progress_callback error at iter {it}]: {_e}")

        rng = random.Random(seed) if seed is not None else None

        start = time.time()
        total_updates = 0
        iterations = 0

        # snapshot initial state
        _tick(0)

        while iterations < max_iter:
            iterations += 1
            any_updated = False
            names = self._ordered_company_names(schedule="random", rng=rng)  # always random

            if verbose:
                print(f"\n--- Iteration {iterations} ---")

            for cname in names:
                comp = self.companies[cname]
                needs = comp.check_update_needed(fp_2024, last_year_sales, fp_2023)
                if needs:
                    if verbose:
                        print(f"{cname} was updated.")
                    comp.update_footprint(fp_2024, fp_2023, last_year_sales)
                    total_updates += 1
                    any_updated = True

            # per-iteration snapshot
            _tick(iterations)

            # fixed point reached → stop immediately
            if not any_updated:
                if verbose:
                    print("\nNo updates needed in this sweep. Converged.")
                break

        elapsed = time.time() - start

        if verbose:
            print(f"\nConverged in {iterations} iterations; total company updates applied: {total_updates}")
            print(f"Time taken: {elapsed:.2f} s")

        # final snapshot (harmless if duplicate)
        _tick(iterations)

        return {"iterations": iterations, "total_updates": total_updates, "seconds": elapsed}



