# SSELF Python/Streamlit prototype for scalable product-footprinting rules

This repository contains the Python and Streamlit prototype accompanying the manuscript:

**Turning LCA Modeling Choices into Scalable Product-Footprinting Rules**

The prototype implements simplified versions of the SSELF framework extensions discussed in the paper. It is intended to demonstrate the proposed data structures, calculation logic, and interface requirements for scalable product footprinting. It is **not** a validated product-footprint calculator and should not be used to estimate empirical product carbon footprints.

## Purpose of the prototype

The prototype was developed as a research instrument to test whether the proposed framework extensions can be represented in executable form. It focuses on architectural and methodological logic rather than complete inventory coverage, empirical validation, or production-ready software engineering.

The prototype demonstrates:

* iterative cradle-to-gate footprint propagation in a synthetic economy;
* a company-in-the-world configuration where one reporting company provides purchases, sales, and direct emissions;
* hierarchical reporting units and carbon-balance checks;
* allocation by alternative product properties;
* system expansion and substitution using product classifications, governed functions, reference units, and last-year market sales data;
* a region-average gate-to-grave extension using a product-class-specific RuleSet.

## Interactive demonstration

A public Streamlit deployment is available here:

**Streamlit app:** [insert Streamlit URL]

The deployed app is provided for demonstration purposes. The GitHub repository and archived release, where available, should be treated as the reference version of the software associated with the manuscript.

## Repository structure

The repository contains Python modules defining the calculation logic and Streamlit pages providing interactive demonstrations.

Typical structure:

```text
Home.py
requirements.txt
README.md

SSELF_python_code/
  SSELF_base.py
  hierarchy_extension.py
  allocation_extension.py
  substitution_extension.py
  marg_substitution_extension.py
  G2G_extension_region.py

pages/
  SSELF_base_app.py
  SSELF_hierarchy_app.py
  SSELF_allocation_app.py
  SSELF_substitution_app.py
  SSELF_G2G_region_app.py

data/
  companies_to_read_updated.csv
  products_to_read_updated.csv
```

Depending on the deployment setup, the module and page files may be organized slightly differently. The Streamlit app assumes that the Python modules are available on the application path.

## Installation

Clone the repository:

```bash
git clone [insert GitHub repository URL]
cd [repository-name]
```

Create and activate a virtual environment:

```bash
python -m venv .venv
```

On macOS/Linux:

```bash
source .venv/bin/activate
```

On Windows:

```bash
.venv\Scripts\activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

## Running the Streamlit app locally

From the repository root, run:

```bash
streamlit run Home.py
```

This launches the Streamlit interface in a browser. The sidebar navigation provides access to the different prototype pages.

## Prototype components

### Base version

The base cradle-to-gate prototype includes two configurations.

The first is a fully synthetic “random world” simulation. In this version, companies, products, purchases, sales, and direct impacts are generated automatically. This configuration is used to test iterative footprint propagation and convergence behaviour.

The second is a “company-in-the-world” configuration. In this version, one reporting company is defined explicitly by the user through its purchases, sales, and direct emissions. This illustrates the kind of information a participating firm would need to provide in a SSELF implementation.

The company-in-the-world configuration can be run with automated updates or manual approval. Automated updates allow footprint values to propagate efficiently. Manual approval illustrates a possible governance configuration in which firms review warranted footprint updates before reporting them.

### Hierarchy extension

The hierarchy extension represents reporting units such as firms, divisions, sites, or internal support functions. Product ownership is defined separately from the organizational hierarchy. The prototype includes carbon-balance diagnostics to check whether modeled burdens are conserved across reporting units and sold outputs.

### Allocation extension

The allocation extension tests how burden assignment changes when allocation is based on alternative product properties, such as sales value, mass, volume, or energy content.

### Substitution extension

The substitution extension implements simplified system expansion and substitution. Product classifications are linked to governed functions and reference units. A secondary co-product is assumed to displace the market-average footprint intensity of products providing the same function in the same reference unit.

The last-year market sales database is treated as a separate data source from the reporting company’s own co-product sales. In the demonstration, synthetic last-year market sales data are used to compute market-average displaced intensities.

### Region-average gate-to-grave extension

The gate-to-grave prototype combines cradle-to-gate footprint propagation with downstream RuleSets. The illustrative t-shirt RuleSet computes retail, use-phase, and end-of-life flows, then multiplies these flows by region-average background intensities.

The example distinguishes between region-average product footprints and consumer-specific footprints. The current implementation focuses on the region-average configuration.

## Data

The prototype uses synthetic and illustrative data only. The included CSV files are input templates or demonstration files. They are not empirical datasets and should not be interpreted as representative of real firms, sectors, products, or product carbon footprints.

## Limitations

This prototype is intended to support the conceptual and methodological argument of the manuscript. It has several important limitations:

* it uses simplified synthetic economies;
* it considers a limited number of illustrative rules and indicators;
* it does not include complete life-cycle inventory coverage;
* it does not validate results against empirical product-footprint studies;
* it is not optimized for production deployment;


## License

This repository is licensed under the Creative Commons Attribution 4.0 International License (CC BY 4.0), unless otherwise noted.

Under CC BY 4.0, users may share and adapt the material, provided appropriate credit is given.

## Contact

For questions about the prototype or associated manuscript, please contact:

Marit Salome Rognan
CIRAIG, Department of Chemical Engineering
École Polytechnique de Montréal
marit-salome.rognan@etud.polymtl.ca
