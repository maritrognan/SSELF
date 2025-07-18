# hierarchy_extension.py

from SSELF_base import Company, Product, System
import pandas as pd
import numpy as np
import random

class HierarchicalCompany(Company):
    """
    Extends the basic Company to handle sub-companies.
    """
    def __init__(self, name, year, purchases=None, sales=None, direct_impacts=None):
        super().__init__(name, year, purchases, sales, direct_impacts)
        self.sub_companies = []

    def add_sub_company(self, sub_company):
        self.sub_companies.append(sub_company)

    def get_total_direct_impacts(self):
        total = self.direct_impacts.sum().sum()
        for sub in self.sub_companies:
            total += sub.get_total_direct_impacts() # GMB - suggestions
        return total

    def update_footprint(self, footprint_db):
        for sub in self.sub_companies:
            sub.update_footprint(footprint_db)
            sub.report_footprint(footprint_db)

        print(f"\nCalculating footprint for {self.name}")

        product_ids = list(self.purchases.index.astype(int))
        footprint_db.data["id"] = footprint_db.data["id"].astype(int)
        scores = footprint_db.data.set_index("id")["scores"]

        valid_ids = [pid for pid in product_ids if pid in scores.index]
        missing_ids = [pid for pid in product_ids if pid not in scores.index]
        if missing_ids:
            print(f"  WARNING: Missing product IDs in footprint DB: {missing_ids}")

        total_impacts = float(scores.loc[valid_ids] @ self.purchases.loc[valid_ids]) if valid_ids else 0
        total_impacts += self.direct_impacts.sum().sum()

        for product in self.products:
            sales_val = self.sales.loc[product.product_id, "Sales"] if product.product_id in self.sales.index else 0
            product_footprint = total_impacts / sales_val if sales_val > 0 else 0
            print(f"  Product {product.name}: Assigned footprint {product_footprint:.4f}")
            product.footprint = product_footprint

        if self.products:
            self.latest_update = float(
                sum(p.footprint * self.sales.loc[p.product_id, "Sales"] for p in self.products)
                / sum(self.sales.loc[p.product_id, "Sales"] for p in self.products)
            )
            print(f"  Final footprint for {self.name}: {self.latest_update:.4f}")

    def check_update_needed(self, footprint_db):
        for sub in self.sub_companies:
            if sub.check_update_needed(footprint_db):
                return True

        previous = self.latest_update
        self.update_footprint(footprint_db)
        new = self.latest_update

        if previous is None or not np.isclose(previous, new, atol=1e-6):
            self.num_updates += 1
            self.report_footprint(footprint_db)
            return True
        return False


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


    def build_from_csv(self, companies_path, products_path):
        df_companies = pd.read_csv(companies_path)
        df_products = pd.read_csv(products_path)

        # Create company instances
        company_objs = {}
        for _, row in df_companies.iterrows():
            cid = row['company_id']
            name = row['name']
            company_objs[cid] = HierarchicalCompany(name, 2024)

        # Link parent-child relations
        for _, row in df_companies.iterrows():
            cid = row['company_id']
            parent_id = row['parent_id']
            if not pd.isna(parent_id):
                company_objs[int(parent_id)].add_sub_company(company_objs[cid])

        # Assign products
        for _, row in df_products.iterrows():
            pid = int(row['product_id'])
            cid = int(row['company_id'])
            pname = row['name']
            product = Product(pid, pname, "unit", company_objs[cid])
            company_objs[cid].add_product(product)

        # Collect top-level companies
        all_subs = {sub for c in company_objs.values() for sub in getattr(c, 'sub_companies', [])}
        top_level = {name: c for name, c in company_objs.items() if c not in all_subs}

        self.companies = top_level
        self.product_id_counter = max(df_products['product_id']) + 1
        self.num_companies = len(self.companies)
        self.num_products = len(df_products)
