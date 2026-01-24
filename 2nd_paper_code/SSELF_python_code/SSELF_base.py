# SSELF_base.py

import pandas as pd
import numpy as np
import random
import time


class Product:
    def __init__(self, product_id, name, unit, company):
        self.product_id = product_id
        self.name = name
        self.unit = unit
        self.company = company
        self.footprint = 0  # placeholder updated during footprint calculation


class Company:
    def __init__(self, name, year, purchases=None, sales=None, direct_impacts=None):
        self.name = name
        self.year = year
        self.purchases = pd.Series(purchases) if purchases is not None else pd.Series(dtype=float)
        self.sales = pd.DataFrame(sales) if sales is not None else pd.DataFrame()
        self.direct_impacts = pd.DataFrame(direct_impacts) if direct_impacts is not None else pd.DataFrame()
        self.products = []
        self.latest_update = None
        self.num_updates = 0

    def add_product(self, product):
        self.products.append(product)

    def update_footprint(self, footprint_db):
        print(f"\nCalculating footprint for {self.name}")

        product_ids = list(self.purchases.index.astype(int))
        footprint_db.data["id"] = footprint_db.data["id"].astype(int)

        missing_ids = [pid for pid in product_ids if pid not in footprint_db.data["id"].values]
        if missing_ids:
            print(f"  WARNING: Missing product IDs in footprint DB: {missing_ids}")
            return

        total_impacts = (footprint_db.data.set_index("id").loc[product_ids, "scores"] @ self.purchases)
        total_impacts += self.direct_impacts.sum().sum()

        for product in self.products:
            product_footprint = (
                total_impacts / self.sales.loc[product.product_id, "Sales"]
                if product.product_id in self.sales.index and self.sales.loc[product.product_id, "Sales"] > 0
                else 0
            )
            print(f"  Product {product.name}: Assigned footprint {product_footprint:.4f}")
            product.footprint = product_footprint

        if self.products:
            self.latest_update = float(
                sum(p.footprint * self.sales.loc[p.product_id, "Sales"] for p in self.products)
                / sum(self.sales.loc[p.product_id, "Sales"] for p in self.products)
            )
            print(f"  Final footprint for {self.name}: {self.latest_update:.4f}")

    def check_update_needed(self, footprint_db, auto_update=True, approval_callback=None, atol=1e-6):
        """
        Decide whether this company's product footprints need updating.

        AUTO mode:
          - compute new intensity
          - if changed: publish now (report_footprint) and count an update

        MANUAL mode (only for user company):
          - compute new intensity
          - if changed: queue an approval event via approval_callback
          - then ROLL BACK local changes so nothing is published/visible until approved
        """
        # Snapshot current state
        previous = getattr(self, "latest_update", None)
        prev_foot = {p.product_id: float(getattr(p, "footprint", 0.0)) for p in getattr(self, "products", [])}

        # Compute candidate new values (this may modify self.*)
        self.update_footprint(footprint_db)
        new = self.latest_update

        changed = (previous is None) or (not np.isclose(previous, new, atol=atol))
        if not changed:
            return False

        is_user_company = bool(getattr(self, "is_user_company", False))

        # MANUAL: only for user company; queue & roll back
        if is_user_company and (auto_update is False):
            if approval_callback is not None:
                approval_callback(self, (0.0 if previous is None else float(previous)), float(new), footprint_db)
            # Roll back so the proposed change doesn't leak into the world yet
            for p in getattr(self, "products", []):
                try:
                    p.footprint = prev_foot[p.product_id]
                except Exception:
                    pass
            self.latest_update = previous
            return True  # change detected (queued), but not published

        # AUTO (or non-user companies): publish immediately
        self.num_updates = getattr(self, "num_updates", 0) + 1
        self.report_footprint(footprint_db)
        return True

    def report_footprint(self, footprint_db):
        for product in self.products:
            footprint_db.report(product.product_id, product.footprint)


class FootprintDatabase:
    def __init__(self, year):
        self.year = year
        self.data = pd.DataFrame({"id": pd.Series(dtype=int), "impact_ids": pd.Series(dtype=str), "scores": pd.Series(dtype=float)})

    ## Should it be renamed "record?" GMB
    def report(self, product_id, footprint):
        """ Saves footprint in the database

        Parameters
        ----------
        product_id : int
            Uniquely identifies the product 
        footprint : float
            Footprint to be recorded
        """        
        # Make everything integers in the IDs internally
        self.data["id"] = self.data["id"].astype(int)

        # Update the footprint if exists
        if product_id in self.data["id"].values:
            self.data.loc[self.data["id"] == product_id, "scores"] = footprint
        else:
            # New entry created otherwise
            new_entry = pd.DataFrame({"id": [product_id], "impact_ids": ["CO2"], "scores": [footprint]})
            self.data = pd.concat([self.data, new_entry], ignore_index=True)

    def get_footprint(self, product_id):
        self.data["id"] = self.data["id"].astype(int)
        record = self.data.loc[self.data["id"] == product_id]
        return record["scores"].values[0] if not record.empty else 0


class System:
    def __init__(self, num_companies, num_products):
        self.num_companies = num_companies
        self.num_products = num_products
        self.companies = {}

        self.use_data = np.ones((num_products, num_companies))
        np.fill_diagonal(self.use_data, 0)

        self.margins_data = [random.uniform(1.28, 1.89) for _ in range(num_companies)]
        self.create_companies()

    def create_companies(self):
        for i in range(self.num_companies):
            purchases = pd.Series(self.use_data[:, i], index=[j + 1 for j in range(self.num_products)])
            sales = {}
            direct_impacts = pd.DataFrame({"kg CO2eq": [np.random.randint(0, 659)]})

            company = Company(f"Company {i+1}", 2024, purchases, sales, direct_impacts)
            product_id = i + 1
            sales[product_id] = self.use_data[:, i].sum() * self.margins_data[i]

            product = Product(product_id, f"Product {product_id}", "unit", company)
            company.add_product(product)
            company.sales = pd.DataFrame.from_dict(sales, orient="index", columns=["Sales"])
            self.companies[f"Company_{i+1}"] = company

    def solve(
            self,
            footprint_db,
            forced_updates=0,
            verbose=True,
            progress_callback=None,
            auto_update=True,  # NEW: True = publish immediately, False = queue approvals for user company
            approval_callback=None,  # NEW: app-provided function to enqueue approvals
            max_iter=5000,  # (optional) guard so we don't loop forever
    ):
        start_time = time.time()
        updates_completed = False
        iteration = 0

        # Optional: snapshot initial state (iteration 0)
        if progress_callback is not None:
            try:
                progress_callback(iteration, self)
            except Exception as e:
                if verbose:
                    print(f"[progress_callback error at iter 0] {e}")

        while not updates_completed and iteration < max_iter:
            iteration += 1
            if verbose:
                print(f"\n--- Iteration {iteration} ---")
            any_company_updated = False

            # ✅ EARLY SNAPSHOT (new block)
            setattr(self, "current_iteration", int(iteration))
            if progress_callback is not None:
                try:
                    progress_callback(iteration, self)
                except Exception as e:
                    if verbose:
                        print(f"[progress_callback error at iter {iteration} (start)] {e}")

            companies_to_check = list(self.companies.keys())
            random.shuffle(companies_to_check)

            for cname in companies_to_check:
                company = self.companies[cname]
                if verbose:
                    print(f"Checking {cname}")
                # >>> pass policy + callback down to the company
                changed = company.check_update_needed(
                    footprint_db,
                    auto_update=auto_update,
                    approval_callback=approval_callback,
                )
                if changed:
                    if verbose:
                        # latest_update may be unchanged in manual mode (we roll back on queue),
                        # so keep the message generic.
                        try:
                            val = float(company.latest_update)
                            print(f"  → Change detected. Latest: {val:.4f}")
                        except Exception:
                            print("  → Change detected.")
                    any_company_updated = True
                elif verbose:
                    print("  → No update needed.")

            # call the hook after each iteration
            if progress_callback is not None:
                try:
                    progress_callback(iteration, self)
                except Exception as e:
                    if verbose:
                        print(f"[progress_callback error at iter {iteration}] {e}")

            updates_completed = not any_company_updated

        if iteration >= max_iter and verbose:
            print(f"\n⚠️ Reached max_iter={max_iter} without full convergence.")

        end_time = time.time()
        print("\n✅ Updates completed.")
        print(f"⏱️ Time taken: {end_time - start_time:.2f} seconds")

        print("\n📦 Final Footprints:")
        for cname, company in self.companies.items():
            try:
                print(f"{cname}: {float(company.latest_update):.4f} kg CO2e/unit")
            except Exception:
                print(f"{cname}: N/A")

        print("\n🔁 Update Count:")
        total_updates = sum(getattr(c, "num_updates", 0) for c in self.companies.values())
        for cname, c in self.companies.items():
            print(f"{cname}: {getattr(c, 'num_updates', 0)} updates")
        print(f"Total updates: {total_updates}")

        for i in range(forced_updates):
            print(f"\n--- Forced Update Iteration {i + 1} ---")
            for cname, company in self.companies.items():
                company.update_footprint(footprint_db)
                company.report_footprint(footprint_db)

        print("\n🗃️ Final footprint database:")
        print(footprint_db.data.sort_values("id"))

    ## Todo: checkupdateneeded()  GMB -Done
    ## Todo: system.solve(error_margin=None, forced_updates=0, etc.) to hide the code from the notebook -Done

