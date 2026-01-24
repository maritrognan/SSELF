# G2G_extension_region.py
"""
SSELF Gate-to-Grave (G2G) extension — Region-average version.

Intent
------
This module implements the *region-average* gate-to-grave architecture described in the manuscript:
- RuleCatalog: maps product classification codes -> RuleSet
- RuleSet: defines post-production stage logic via FlowSpec objects
- FlowSpec: declares a parameter/flow, its unit, source (producer/default), and how to value it
- GateToGraveCalculator: executes a RuleSet for a product in a region/year, combining:
    (i) cradle-to-gate footprints from FootprintRepository
    (ii) producer-provided parameters (where required)
    (iii) rule defaults
    (iv) region-average "generic" footprints pulled from AggregatedMarketDatabase (NOT ImpactFactorDB)


Region-average footprint output
-------------------------------
The computed cradle-to-grave result is *region-average* for a product in a region/year, so it can
optionally be stored (reported) back into FootprintRepository for that product_id, impact_id, year.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple, Any, Mapping, Protocol

from SSELF_base import Product  # your base class
from hierarchy_extension import HierarchicalCompany as HCompany
from substitution_extension import ClassificationDatabase


# --------------------------------------------------------------------------------------
# Minimal interfaces (adapt to match your actual classes)
# --------------------------------------------------------------------------------------

ImpactID = str
RegionID = str
ClassCode = str
Year = int


class FootprintRepositoryLike(Protocol):
    """Adapter protocol for your FootprintRepository (from SSELF_base.py).

    Required capability:
    - get(product_id, impact_id, year) -> float
    - report(product_id, impact_id, year, value) -> None
    """

    def get(self, product_id: Any, impact_id: ImpactID, year: Year) -> float: ...
    def report(self, product_id: Any, impact_id: ImpactID, year: Year, value: float) -> None: ...

class GateToGraveCalculator:
    def __init__(
        self,
        rule_catalog: RuleCatalog,
        market_db: AggregatedMarketDatabaseLike,
        class_db: ClassificationDatabase | None = None,
    ):
        self.rule_catalog = rule_catalog
        self.market_db = market_db
        self.class_db = class_db


class G2GProduct(Product):
    def __init__(
        self,
        product_id,
        name,
        unit,
        company,
        *,
        region: str,
        class_code: str,
        primary: bool = True,
        function: str | None = None,
        function_output: float | None = None,
        constrained: bool = False,
        lifetime_declared: float | None = None,
        g2g_inputs: dict[str, float] | None = None,
        g2g_emissions: dict[str, float] | None = None,
    ):
        super().__init__(product_id, name, unit, company)

        # Identification / classification
        self.region = region
        self.class_code = class_code     # FK -> ClassificationDatabase
        self.primary = primary

        # Functionality (ideally derived from ClassificationDB; optional cache)
        self.function = function         # FK-ish -> ClassificationDatabase.function
        self.function_output = function_output

        # Substitution-related (if you reuse the same Product across extensions)
        self.constrained = constrained

        # Gate-to-grave support
        self.lifetime_declared = lifetime_declared
        self.g2g_inputs = g2g_inputs or {}      # producer-provided or declared attrs for G2G
        self.g2g_emissions = g2g_emissions or {}  # optional: direct use-phase emissions etc.



class G2GCompany(HCompany):
    def __init__(self, company_id, name, *args, **kwargs):
        super().__init__(company_id, name, *args, **kwargs)


    def set_g2g_input(self, product_id, key: str, value: float):
        # store either on the product or in a company dict
        prod = self.get_product(product_id)  # adapt to your API
        prod.g2g_inputs[key] = float(value)

    def compute_g2g_label(self, product_id, *, calc, repo, year, impact_id: str):
        prod = self.get_product(product_id)
        return calc.compute_g2g_footprint(
            product=prod,
            repo=repo,
            year=year,
            region=prod.region,
            impact_id=impact_id,
            producer_params=prod.g2g_inputs,
            store_result=True,  # “label” stored in repo
        )


class AggregatedMarketDatabaseLike(Protocol):
    """Public, accessible market-average 'intensities' used to value generic/average flows.

    Needed capability (choose one of these patterns and implement accordingly):
    A) get_intensity(flow_key, region_id, impact_id, year) -> float
    B) get_intensity_by_function(function_id, region_id, impact_id, year) -> float
    C) get_intensity_by_class_code(class_code, region_id, impact_id, year) -> float

    Here we implement A) with a generic key, and keep helper methods.
    """

    def get_intensity(self, key: str, region_id: RegionID, impact_id: ImpactID, year: Year) -> float: ...


# --------------------------------------------------------------------------------------
# Core data structures
# --------------------------------------------------------------------------------------

class ParamSource(str, Enum):
    PRODUCER_REQUIRED = "producer_required"
    PRODUCER_OPTIONAL = "producer_optional"
    RULE_DEFAULT = "rule_default"


class Stage(str, Enum):
    RETAIL = "retail"
    USE = "use"
    EOL = "eol"


@dataclass(frozen=True)
class FlowSpec:
    """
    One "line item" in a gate-to-grave rule.

    Think of FlowSpec as: "this contribution exists; here is how to quantify it; here is how to value it."

    Examples:
    - Electricity use during use-phase: quantity (kWh) from default or producer; valued by market intensity key "electricity_kwh"
    - Detergent use: quantity (kg) default; valued by market intensity key "detergent_generic_kg"
    - EoL treatment: quantity may be mass (kg) from producer; valued by intensity key "textile_eol_landfill_kg"
    """

    flow_id: str
    stage: Stage

    # Quantity definition
    quantity_unit: str
    quantity_source: ParamSource
    default_quantity: Optional[float] = None

    # If producer provides quantity, which parameter name is expected?
    producer_param_name: Optional[str] = None

    # Valuation definition: how to convert quantity -> impacts
    # We avoid ImpactFactorDB: valuation is pulled from AggregatedMarketDatabase by an intensity_key
    intensity_key: str = ""

    # Optional: allow rule to scale quantity by a product-specific attribute (e.g., lifespan, wash_count)
    # In region-average mode these scalars can be defaults, or producer-provided.
    scalar_param_name: Optional[str] = None
    default_scalar: float = 1.0

    # Optional: impacts covered by this FlowSpec. If None -> apply to all impacts requested by calculator.
    applicable_impacts: Optional[List[ImpactID]] = None

    def resolve_quantity(
        self,
        producer_params: Mapping[str, float],
        rule_params: Mapping[str, float],
    ) -> float:
        """Compute the final quantity for this flow."""
        # Determine base quantity
        if self.quantity_source == ParamSource.RULE_DEFAULT:
            if self.default_quantity is None:
                raise ValueError(f"FlowSpec '{self.flow_id}' needs a default_quantity.")
            base_qty = float(self.default_quantity)

        elif self.quantity_source in (ParamSource.PRODUCER_REQUIRED, ParamSource.PRODUCER_OPTIONAL):
            if not self.producer_param_name:
                raise ValueError(f"FlowSpec '{self.flow_id}' needs producer_param_name.")
            if self.producer_param_name in producer_params:
                base_qty = float(producer_params[self.producer_param_name])
            else:
                if self.quantity_source == ParamSource.PRODUCER_REQUIRED:
                    raise KeyError(
                        f"Missing required producer parameter '{self.producer_param_name}' "
                        f"for FlowSpec '{self.flow_id}'."
                    )
                # optional -> fall back to default if present, else 0
                base_qty = float(self.default_quantity or 0.0)

        else:
            raise ValueError(f"Unknown quantity_source: {self.quantity_source}")

        # Apply scalar if defined
        scalar = self.default_scalar
        if self.scalar_param_name:
            if self.scalar_param_name in producer_params:
                scalar = float(producer_params[self.scalar_param_name])
            elif self.scalar_param_name in rule_params:
                scalar = float(rule_params[self.scalar_param_name])
            # else keep default_scalar

        return base_qty * scalar


@dataclass
class RuleSet:
    """
    A concrete rule implementation (e.g., 'Apparel & Footwear vX.Y inspired dummy rule').

    Stores:
    - metadata (name, region applicability, version)
    - list of FlowSpecs describing retail/use/eol contributions
    - optional rule-level parameters (e.g., default lifespan, default wash count)
    """

    rule_set_id: str
    name: str
    version: str
    valid_regions: List[RegionID] = field(default_factory=list)

    # Rule-level default parameters (used by FlowSpecs if scalar_param_name references these)
    rule_params: Dict[str, float] = field(default_factory=dict)

    # Flow specs
    flows: List[FlowSpec] = field(default_factory=list)

    def assert_region_supported(self, region_id: RegionID) -> None:
        if self.valid_regions and region_id not in self.valid_regions:
            raise ValueError(
                f"RuleSet '{self.rule_set_id}' not valid for region '{region_id}'. "
                f"Valid regions: {self.valid_regions}"
            )

    def flows_for_stage(self, stage: Stage) -> List[FlowSpec]:
        return [f for f in self.flows if f.stage == stage]


@dataclass
class RuleCatalog:
    """
    Maps classification codes to RuleSets.

    This can be:
    - direct mapping code -> rule_set_id
    - mapping via code prefixes (e.g., HS 61xx -> apparel), if you want.

    Here: direct mapping + optional prefix mapping.
    """

    rulesets: Dict[str, RuleSet] = field(default_factory=dict)
    class_to_ruleset: Dict[ClassCode, str] = field(default_factory=dict)
    prefix_to_ruleset: Dict[str, str] = field(default_factory=dict)  # e.g., "61" -> "apparel_rule"

    def register_ruleset(self, ruleset: RuleSet) -> None:
        self.rulesets[ruleset.rule_set_id] = ruleset

    def map_class_code(self, class_code: ClassCode, rule_set_id: str) -> None:
        self.class_to_ruleset[class_code] = rule_set_id

    def map_prefix(self, prefix: str, rule_set_id: str) -> None:
        self.prefix_to_ruleset[prefix] = rule_set_id

    def resolve_ruleset(self, class_code: ClassCode) -> RuleSet:
        # direct mapping
        if class_code in self.class_to_ruleset:
            rid = self.class_to_ruleset[class_code]
            return self.rulesets[rid]

        # prefix mapping (longest prefix wins)
        matched: Optional[Tuple[str, str]] = None
        for pref, rid in self.prefix_to_ruleset.items():
            if class_code.startswith(pref):
                if matched is None or len(pref) > len(matched[0]):
                    matched = (pref, rid)
        if matched:
            return self.rulesets[matched[1]]

        raise KeyError(f"No RuleSet found for class_code '{class_code}'.")


# --------------------------------------------------------------------------------------
# Calculator
# --------------------------------------------------------------------------------------

ImpactVector = Dict[ImpactID, float]


@dataclass
class GateToGraveCalculator:
    """
    Region-average G2G calculator:
    - Uses RuleCatalog to select RuleSet based on product.class_code
    - Pulls cradle-to-gate impacts from FootprintRepository
    - Values rule flows using AggregatedMarketDatabase intensities
    - Returns an ImpactVector and (optionally) writes it back to FootprintRepository
    """

    rule_catalog: RuleCatalog
    market_db: AggregatedMarketDatabaseLike

    # Optional: if you want function_id lookups or reference units
    class_db: Optional[ClassificationDatabaseLike] = None

    def compute_g2g_footprint(
        self,
        product: G2GProduct,
        repo: FootprintRepositoryLike,
        year: Year,
        region_id: RegionID,
        impacts: List[ImpactID],
        producer_params: Optional[Mapping[str, float]] = None,
        store_result: bool = False,
    ) -> ImpactVector:
        """
        Compute cradle-to-grave impacts for a product in a region/year.

        Parameters
        ----------
        product : G2GProduct
        repo : FootprintRepositoryLike
            Must already contain cradle-to-gate footprints for product.product_id.
        year : int
        region_id : str
        impacts : list[str]
            Which impact categories to compute.
        producer_params : dict[str, float]
            Producer-provided post-production params required/optional by rule FlowSpecs.
            Example keys: "detergent_kg_per_life", "lifetime_washes", "electricity_kwh_per_use", etc.
        store_result : bool
            If True, writes computed cradle-to-grave impacts into repo via repo.report.

        Returns
        -------
        ImpactVector: dict impact_id -> value
        """
        producer_params = producer_params or {}

        ruleset = self.rule_catalog.resolve_ruleset(product.class_code)
        ruleset.assert_region_supported(region_id)

        # Start from cradle-to-gate
        total: ImpactVector = {}
        for imp in impacts:
            total[imp] = float(repo.get(product.product_id, imp, year))

        # Add retail/use/EoL contributions as defined by RuleSet
        for flow in ruleset.flows:
            qty = flow.resolve_quantity(producer_params=producer_params, rule_params=ruleset.rule_params)

            # Decide which impacts this flow contributes to
            applicable = flow.applicable_impacts or impacts

            for imp in applicable:
                intensity = float(self.market_db.get_intensity(flow.intensity_key, region_id, imp, year))
                total[imp] = total.get(imp, 0.0) + qty * intensity

        if store_result:
            for imp, val in total.items():
                repo.report(product.product_id, imp, year, val)

        return total


# --------------------------------------------------------------------------------------
# Helpers: dummy rule inspired by a PEFCR-like structure (for prototyping)
# --------------------------------------------------------------------------------------

def build_dummy_tshirt_ruleset(rule_set_id: str = "rule_tshirt_v0") -> RuleSet:
    """
    A minimal "dummy-ish" ruleset inspired by typical apparel logic.
    You can adjust quantities/units later. The point is structure.

    Assumptions (illustrative only):
    - Retail: average last-mile + store energy per item (default)
    - Use: detergent kg + electricity kWh scaled by wash_count (default wash_count)
    - EoL: default mass-based treatment (producer can optionally provide mass_kg)
    """
    return RuleSet(
        rule_set_id=rule_set_id,
        name="Dummy T-shirt rule (PEFCR-inspired)",
        version="0.1",
        valid_regions=["EU"],  # change as needed
        rule_params={
            "wash_count": 30.0,      # default lifetime washes
        },
        flows=[
            # Retail (defaults)
            FlowSpec(
                flow_id="retail_store_energy",
                stage=Stage.RETAIL,
                quantity_unit="item",
                quantity_source=ParamSource.RULE_DEFAULT,
                default_quantity=1.0,
                intensity_key="retail_energy_per_item",
            ),
            # Use phase (defaults + scalar)
            FlowSpec(
                flow_id="use_detergent",
                stage=Stage.USE,
                quantity_unit="kg",
                quantity_source=ParamSource.RULE_DEFAULT,
                default_quantity=0.002,  # kg detergent per wash (illustrative)
                intensity_key="detergent_generic_kg",
                scalar_param_name="wash_count",
                default_scalar=30.0,
            ),
            FlowSpec(
                flow_id="use_electricity",
                stage=Stage.USE,
                quantity_unit="kWh",
                quantity_source=ParamSource.RULE_DEFAULT,
                default_quantity=0.6,   # kWh per wash (illustrative)
                intensity_key="electricity_kwh",
                scalar_param_name="wash_count",
                default_scalar=30.0,
            ),
            # End-of-life (optional producer mass, else default)
            FlowSpec(
                flow_id="eol_treatment",
                stage=Stage.EOL,
                quantity_unit="kg",
                quantity_source=ParamSource.PRODUCER_OPTIONAL,
                default_quantity=0.2,  # default garment mass kg if not provided
                producer_param_name="mass_kg",
                intensity_key="textile_eol_avg_kg",
            ),
        ],
    )


def build_region_average_rule_catalog() -> RuleCatalog:
    """
    Creates a RuleCatalog with one dummy apparel rule and a mapping for HS-like code "6109".
    Adjust to match how you store HS codes (e.g., "6109" or "HS 6109").
    """
    catalog = RuleCatalog()
    tshirt = build_dummy_tshirt_ruleset()
    catalog.register_ruleset(tshirt)

    # Direct mapping for a single code
    catalog.map_class_code("6109", tshirt.rule_set_id)

    # Optionally, map a prefix (e.g., all "61" knit apparel -> same dummy rule)
    # catalog.map_prefix("61", tshirt.rule_set_id)

    return catalog


# --------------------------------------------------------------------------------------
# Optional: simple in-memory market DB (for notebooks / tests)
# --------------------------------------------------------------------------------------

@dataclass
class InMemoryAggregatedMarketDatabase(AggregatedMarketDatabaseLike):
    """
    Very simple public market DB for intensities:
    intensities[(key, region, impact_id, year)] = intensity per unit of FlowSpec.quantity_unit
    """
    intensities: Dict[Tuple[str, RegionID, ImpactID, Year], float] = field(default_factory=dict)

    def set_intensity(self, key: str, region_id: RegionID, impact_id: ImpactID, year: Year, value: float) -> None:
        self.intensities[(key, region_id, impact_id, year)] = float(value)

    def get_intensity(self, key: str, region_id: RegionID, impact_id: ImpactID, year: Year) -> float:
        try:
            return self.intensities[(key, region_id, impact_id, year)]
        except KeyError as e:
            raise KeyError(
                f"Missing market intensity for (key='{key}', region='{region_id}', impact='{impact_id}', year={year})."
            ) from e


# --------------------------------------------------------------------------------------
# Example usage (keep in notebook, not in production module)
# --------------------------------------------------------------------------------------
# if __name__ == "__main__":
#     # Build catalog + dummy market DB
#     catalog = build_region_average_rule_catalog()
#     market = InMemoryAggregatedMarketDatabase()
#     market.set_intensity("retail_energy_per_item", "EU", "GWP100", 2026, 0.5)
#     market.set_intensity("detergent_generic_kg", "EU", "GWP100", 2026, 2.0)
#     market.set_intensity("electricity_kwh", "EU", "GWP100", 2026, 0.25)
#     market.set_intensity("textile_eol_avg_kg", "EU", "GWP100", 2026, 1.2)
#
#     calc = GateToGraveCalculator(rule_catalog=catalog, market_db=market)
#     # You would pass your real Product + FootprintRepository instances here.
