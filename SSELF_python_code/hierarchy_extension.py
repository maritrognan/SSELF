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
            total += sub.direct_impacts.sum().sum()
        return total


class HierarchicalSystem(System):
    """
    Extends the basic System to support hierarchical sub-entities.
    """
    def __init__(self, num_companies, num_products):
        super().__init__(num_companies, num_products)
        self.product_id_counter = num_products + 1  # track next available product ID

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
