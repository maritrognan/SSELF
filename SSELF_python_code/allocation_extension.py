# allocation_extension.py

import pandas as pd
import numpy as np
import random
from hierarchy_extension import Product, Company, System

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

        product_ids = list(self.purchases.index.astype(int))
        footprint_db.data["id"] = footprint_db.data["id"].astype(int)

        missing_ids = [pid for pid in product_ids if pid not in footprint_db.data["id"].values]
        if missing_ids:
            print(f"  WARNING: Missing product IDs in footprint DB: {missing_ids}")
            return

        total_impacts = (footprint_db.data.set_index("id").loc[product_ids, "scores"] @ self.purchases)
        total_impacts += self.direct_impacts.sum().sum()

        allocation_basis = None
        total_alloc = 0
        for basis in ["mass", "volume", "energy"]:
            total = sum(p.properties.get(basis, 0) for p in self.products)
            if total > 0:
                allocation_basis = basis
                total_alloc = total
                break

        if allocation_basis:
            print(f"  Using {allocation_basis} allocation")
            for product in self.products:
                ref_value = product.properties.get(allocation_basis, 0)
                product.footprint = (total_impacts * ref_value / total_alloc) if total_alloc > 0 else 0
                print(f"  Product {product.name} ({allocation_basis.capitalize()}={ref_value}): Assigned footprint {product.footprint:.4f}")
        else:
            print("  Using economic allocation")
            for product in self.products:
                product_footprint = (
                    total_impacts / self.sales.loc[product.product_id, "Sales"]
                    if product.product_id in self.sales.index and self.sales.loc[product.product_id, "Sales"] > 0
                    else 0
                )
                print(f"  Product {product.name} (Sales={self.sales.loc[product.product_id, 'Sales'] if product.product_id in self.sales.index else 'N/A'}): Assigned footprint {product_footprint:.4f}")
                product.footprint = product_footprint

        if self.products:
            self.latest_update = float(
                sum(p.footprint * self.sales.loc[p.product_id, "Sales"] for p in self.products)
                / sum(self.sales.loc[p.product_id, "Sales"] for p in self.products)
            )
            print(f"  Final footprint for {self.name}: {self.latest_update:.4f}")

    def report_footprint(self, footprint_db):
        for product in self.products:
            footprint_db.report(product.product_id, product.footprint)

class AllocationSystem(System):
    def __init__(self, num_companies, num_products):
        super().__init__(num_companies, num_products)
        self.product_id_counter = num_products + 1

    def create_companies(self):
        for i in range(self.num_companies):
            purchases = pd.Series(self.use_data[:, i], index=[j + 1 for j in range(self.num_products)])
            sales = {}
            direct_impacts = pd.DataFrame({"kg CO2eq": [np.random.randint(0, 659)]})

            company = AllocationCompany(f"Company_{i+1}", 2024, purchases, sales, direct_impacts)
            product_id = i + 1
            sales[product_id] = self.use_data[:, i].sum() * self.margins_data[i]

            product = AllocationProduct(product_id, f"Product {product_id}", "unit", company, properties={})
            company.add_product(product)
            company.sales = pd.DataFrame.from_dict(sales, orient="index", columns=["Sales"])
            self.companies[f"Company_{i+1}"] = company
