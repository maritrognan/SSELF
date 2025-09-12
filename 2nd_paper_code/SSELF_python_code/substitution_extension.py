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
        scores = fp_db_2024.data.set_index("id")["scores"]
        valid_ids = [i for i in self.purchases.index if i in scores.index]
        total_impacts = float(scores.loc[valid_ids] @ self.purchases.loc[valid_ids]) + self.direct_impacts.sum().sum()

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
        data = sales_db.get_sales_data_by_class_code(class_code)
        if data.empty:
            return 0
        fp_db.data["id"] = fp_db.data["id"].astype(int)
        merged = data.merge(fp_db.data, on="id", how="left").fillna(0)

        total = 0
        weight = 0
        for _, row in merged.iterrows():
            conv = row["function_output"]
            total += row["sales_volume"] * row["scores"] * conv
            weight += row["sales_volume"] * conv
        print(f"  [DEBUG] Average footprint for {class_code}: total impact = {total:.4f}, weight = {weight:.4f}")

        return total / weight if weight > 0 else 0

    def check_update_needed(self, fp_db_2024, last_year_sales_db, fp_db_2023):
        scores = fp_db_2024.data.set_index("id")["scores"]
        valid_ids = [i for i in self.purchases.index if i in scores.index]

        total = float(scores.loc[valid_ids] @ self.purchases.loc[valid_ids]) + self.direct_impacts.sum().sum()

        primary_impacts = {}
        for product in self.products:
            sales_vol = self.sales.loc[product.product_id, "Sales"] if product.product_id in self.sales.index else 0
            if product.primary_secondary == "primary":
                primary_impacts[product.product_id] = total
            else:
                avg = self.get_average_footprint(product.class_code, last_year_sales_db, fp_db_2023)
                sub_impact = avg * sales_vol * product.function_output
                for p in self.products:
                    if p.primary_secondary == "primary":
                        primary_impacts[p.product_id] -= sub_impact

        # Compare product-level footprints instead
        for product in self.products:
            if product.primary_secondary == "primary":
                vol = self.sales.loc[product.product_id, "Sales"] if product.product_id in self.sales.index else 0
                new_fp = primary_impacts[product.product_id] / vol if vol else 0
            else:
                avg = self.get_average_footprint(product.class_code, last_year_sales_db, fp_db_2023)
                vol = self.sales.loc[product.product_id, "Sales"]
                new_fp = avg * product.function_output if vol else 0

            current_fp = getattr(product, "footprint", 0)

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
            atol=1e-6,  # kept for consistency with other versions, not passed through (your check uses 1e-6)
            max_iter=200,
            schedule="random",  # "random" or "cyclic"
            seed=None,  # set for reproducible random order
            verbose=True,
            patience=1,  # stop after this many consecutive no-update sweeps
    ):
        rng = random.Random(seed) if seed is not None else None

        start = time.time()
        total_updates = 0
        iterations = 0
        no_update_streak = 0

        while iterations < max_iter:
            iterations += 1
            any_updated = False
            names = self._ordered_company_names(schedule=schedule, rng=rng)

            if verbose:
                print(f"\n--- Iteration {iterations} ---")

            for cname in names:
                comp = self.companies[cname]

                # reuse your existing detection logic
                needs = comp.check_update_needed(fp_2024, last_year_sales, fp_2023)

                if needs:
                    if verbose:
                        print(f"{cname} was updated.")
                    # reuse your existing updater
                    comp.update_footprint(fp_2024, fp_2023, last_year_sales)
                    total_updates += 1
                    any_updated = True

            if not any_updated:
                no_update_streak += 1
            else:
                no_update_streak = 0

            if no_update_streak >= patience:
                if verbose:
                    print(f"\nNo updates needed for {no_update_streak} consecutive iteration(s). Converged.")
                break

        elapsed = time.time() - start

        if verbose:
            print(f"\nConverged in {iterations} iterations; total company updates applied: {total_updates}")
            print(f"Time taken: {elapsed:.2f} s")

        return {"iterations": iterations, "total_updates": total_updates, "seconds": elapsed}

