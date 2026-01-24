# allocation_extension.py

import pandas as pd
import numpy as np
import random

# Base (non-hierarchical) primitives
from SSELF_base import Product, Company, System

# Hierarchy engine/types
from hierarchy_extension import (
    HierarchicalCompany as HCompany,
    HierarchicalSystem as HSystem,
)

class AllocationHierarchicalCompany(HCompany):
    """
    Hierarchical company that allocates impacts by a selected basis (mass/volume/energy/economic)
    and always stores product.footprint as INTENSITY (kg CO2e / $).
    """
    def __init__(self, name, year, purchases=None, sales=None, direct_impacts=None):
        super().__init__(name, year, purchases, sales, direct_impacts)
        self.preferred_basis = None  # "mass" | "volume" | "energy" | "economic" | None

    def update_footprint(self, footprint_db):
        # NOTE: HCompany.check_update_needed(...) handles children first.
        print(f"\nCalculating footprint for {self.name}")

        # --- Align purchases to int product IDs ---
        if isinstance(self.purchases, pd.Series) and not self.purchases.empty:
            try:
                pur = self.purchases.copy()
                pur.index = pur.index.astype(int)
            except Exception:
                pur = pd.Series(dtype=float)
        else:
            pur = pd.Series(dtype=float)

        # --- Scores from DB ---
        scores = pd.Series(dtype=float)
        if not footprint_db.data.empty:
            df = footprint_db.data.copy()
            df["id"] = df["id"].astype(int)
            scores = df.set_index("id")["scores"]

        # Embedded + direct
        common = pur.index.intersection(scores.index)
        total_impacts = float(pur.loc[common] @ scores.loc[common]) if len(common) else 0.0
        if isinstance(self.direct_impacts, pd.DataFrame) and not self.direct_impacts.empty:
            try:
                total_impacts += float(self.direct_impacts.sum(numeric_only=True).sum())
            except Exception:
                pass

        # --- Sales helper ---
        sales_df = self.sales if isinstance(self.sales, pd.DataFrame) else pd.DataFrame()
        if not sales_df.empty and "Sales" not in sales_df.columns and sales_df.shape[1] == 1:
            sales_df = sales_df.rename(columns={sales_df.columns[0]: "Sales"})
            self.sales = sales_df

        own_products = list(getattr(self, "products", []))

        def sales_val(pid: int) -> float:
            if not isinstance(self.sales, pd.DataFrame) or "Sales" not in self.sales.columns:
                return 0.0
            if pid not in self.sales.index:
                return 0.0
            try:
                return float(self.sales.loc[pid, "Sales"])
            except Exception:
                return 0.0

        total_sales_this_company = float(sum(sales_val(p.product_id) for p in own_products))

        # basis (company override → system → default economic)
        forced = getattr(self, "preferred_basis", None)
        if forced is None:
            forced = getattr(getattr(self, "system", None), "preferred_basis", None)

        def economic_allocation():
            if total_sales_this_company > 0:
                intensity = total_impacts / total_sales_this_company
                for p in own_products:
                    p.footprint = float(intensity)  # kg CO2e / $
                    print(f"  Product {p.name}: Assigned footprint {p.footprint:.4f}")
                self.latest_update = float(intensity)
            else:
                for p in own_products:
                    p.footprint = 0.0
                self.latest_update = 0.0
            print(f"  Final footprint for {self.name}: {self.latest_update:.4f}")

        def property_allocation(basis: str) -> bool:
            total_prop = float(sum(float(getattr(p, "properties", {}).get(basis, 0.0)) for p in own_products))
            if total_prop <= 0.0:
                print(f"  Requested {basis} allocation but total {basis}=0 → fallback to economic")
                return False
            print(f"  Using {basis} allocation")
            for p in own_products:
                ref_val = float(getattr(p, "properties", {}).get(basis, 0.0))
                # absolute allocation in kg CO2e
                allocated = total_impacts * (ref_val / total_prop) if total_prop > 0 else 0.0
                s = sales_val(p.product_id)
                p.footprint = (allocated / s) if s > 0 else 0.0  # convert to intensity
                print(f"  Product {p.name} ({basis.capitalize()}={ref_val}, Sales={s}): "
                      f"Assigned footprint {p.footprint:.4f}")
            # sales-weighted avg intensity
            if total_sales_this_company > 0:
                self.latest_update = float(
                    sum(p.footprint * sales_val(p.product_id) for p in own_products) / total_sales_this_company
                )
            else:
                self.latest_update = 0.0
            print(f"  Final footprint for {self.name}: {self.latest_update:.4f}")
            return True

        if forced in {"mass", "volume", "energy"}:
            if not property_allocation(forced):
                economic_allocation()
        elif forced == "economic":
            economic_allocation()
        else:
            # No Auto ranking here; default to economic if nothing is forced
            economic_allocation()

class AllocationHierarchicalSystem(HSystem):
    """
    Same engine as HierarchicalSystem, but instantiate AllocationHierarchicalCompany + AllocationProduct.
    """
    def __init__(self, num_companies=None, num_products=None):
        super().__init__(num_companies, num_products)
        self.preferred_basis = None  # "mass" | "volume" | "energy" | "economic"

    def create_companies(self):
        for i in range(self.num_companies):
            purchases = pd.Series(self.use_data[:, i], index=[j + 1 for j in range(self.num_products)])
            sales = {}
            direct_impacts = pd.DataFrame({"kg CO2eq": [np.random.randint(0, 659)]})

            company = AllocationHierarchicalCompany(f"Company {i+1}", 2024, purchases, sales, direct_impacts)
            company.system = self  # backref for basis lookup
            product_id = i + 1
            sales[product_id] = self.use_data[:, i].sum() * self.margins_data[i]

            # Use AllocationProduct to carry properties
            product = AllocationProduct(product_id, f"Product {product_id}", "unit", company, properties={})
            company.add_product(product)
            company.sales = pd.DataFrame.from_dict(sales, orient="index", columns=["Sales"])

            self.companies[f"Company_{i+1}"] = company


class AllocationProduct(Product):
    def __init__(self, product_id, name, unit, company, properties=None):
        super().__init__(product_id, name, unit, company)
        self.properties = properties or {}  # Dictionary like {"mass": 100, "energy": 300}

class AllocationCompany(Company):
    def __init__(self, name, year, purchases=None, sales=None, direct_impacts=None):
        super().__init__(name, year, purchases, sales, direct_impacts)
        self.sub_companies = []

    def add_sub_company(self, sub_company):
        self.sub_companies.append(sub_company)

    def update_footprint(self, footprint_db):
        print(f"\nCalculating footprint for {self.name}")

        # --- choose basis (forced -> auto-detect -> fallback economic)
        forced = getattr(self, "preferred_basis", None)
        if forced is None:
            forced = getattr(getattr(self, "system", None), "preferred_basis", None)

        # compute total upstream impacts same as before
        product_ids = list(self.purchases.index.astype(int))
        footprint_db.data["id"] = footprint_db.data["id"].astype(int)

        missing_ids = [pid for pid in product_ids if pid not in footprint_db.data["id"].values]
        if missing_ids:
            print(f"  WARNING: Missing product IDs in footprint DB: {missing_ids}")
            return

        total_impacts = (footprint_db.data.set_index("id").loc[product_ids, "scores"] @ self.purchases)
        total_impacts += self.direct_impacts.sum().sum()

        def _economic_allocation():
            print("  Using economic allocation")
            for product in self.products:
                sales_val = (
                    self.sales.loc[product.product_id, "Sales"]
                    if product.product_id in self.sales.index else 0
                )
                product.footprint = (total_impacts / sales_val) if sales_val > 0 else 0.0
                print(f"  Product {product.name} (Sales={sales_val}): Assigned footprint {product.footprint:.4f}")

        def _property_allocation(basis: str):
            total_alloc = sum(float(product.properties.get(basis, 0.0)) for product in self.products)
            if total_alloc <= 0:
                print(f"  Requested {basis} allocation but total {basis}=0 → fallback to economic")
                return False
            print(f"  Using {basis} allocation")
            for product in self.products:
                ref_val = float(product.properties.get(basis, 0.0))
                # absolute allocation (kg CO2e)
                allocated = (total_impacts * ref_val / total_alloc)
                # convert to intensity (kg CO2e / $)
                sales_val = (
                    self.sales.loc[product.product_id, "Sales"]
                    if product.product_id in self.sales.index else 0.0
                )
                product.footprint = (allocated / sales_val) if sales_val > 0 else 0.0
                print(
                    f"  Product {product.name} ({basis.capitalize()}={ref_val}, Sales={sales_val}): "
                    f"Assigned footprint {product.footprint:.4f}"
                )
            return True

        # Forced basis takes priority
        if forced in {"mass", "volume", "energy"}:
            ok = _property_allocation(forced)
            if not ok:
                _economic_allocation()
        elif forced == "economic":
            _economic_allocation()
        else:
            # Auto-detect (original behavior)
            for basis in ["mass", "volume", "energy"]:
                if any(float(p.properties.get(basis, 0.0)) > 0 for p in self.products):
                    _property_allocation(basis)
                    break
            else:
                _economic_allocation()

        if self.products:
            self.latest_update = float(
                sum(p.footprint * self.sales.loc[p.product_id, "Sales"] for p in self.products)
                / max(sum(self.sales.loc[p.product_id, "Sales"] for p in self.products), 1e-12)
            )
            print(f"  Final footprint for {self.name}: {self.latest_update:.4f}")

    def report_footprint(self, footprint_db):
        for product in self.products:
            footprint_db.report(product.product_id, product.footprint)

class AllocationSystem(System):
    def __init__(self, num_companies, num_products):
        super().__init__(num_companies, num_products)
        self.product_id_counter = num_products + 1
        self.preferred_basis = None  # "mass" | "volume" | "energy" | "economic" | None

    def create_companies(self):
        for i in range(self.num_companies):
            purchases = pd.Series(self.use_data[:, i], index=[j + 1 for j in range(self.num_products)])
            sales = {}
            direct_impacts = pd.DataFrame({"kg CO2eq": [np.random.randint(0, 659)]})

            company = AllocationCompany(f"Company_{i+1}", 2024, purchases, sales, direct_impacts)
            company.system = self  # <--- back-ref for basis lookup
            product_id = i + 1
            sales[product_id] = self.use_data[:, i].sum() * self.margins_data[i]

            product = AllocationProduct(product_id, f"Product {product_id}", "unit", company, properties={})
            company.add_product(product)
            company.sales = pd.DataFrame.from_dict(sales, orient="index", columns=["Sales"])
            self.companies[f"Company_{i+1}"] = company
