# G2G_extension_region.py
"""
SSELF Gate-to-Grave extension — Region-average version.

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
from typing import Dict, List, Optional, Tuple, Any, Mapping, Protocol, Callable


from hierarchy_extension import HierarchicalCompany as HCompany
from substitution_extension import ClassificationDatabase

from SSELF_base import Product as BaseProduct, FootprintDatabase



# --------------------------------------------------------------------------------------
# Minimal interfaces (adapt to match your actual classes)
# --------------------------------------------------------------------------------------

ImpactID = str
RegionID = str
ClassCode = str
Year = int
ComputeFlowsFn = Callable[["RuleSet", Mapping[str, float]], tuple[Dict[str, float], Optional[float]]]




class G2GProduct(BaseProduct):
    def __init__(self, product_id, name, unit, company, class_code, g2g_inputs=None):
        super().__init__(product_id, name, unit, company)
        self.class_code = class_code
        self.g2g_inputs = g2g_inputs or {}

class G2GCompany(HCompany):
    def __init__(self, company_id, name, *args, **kwargs):
        super().__init__(company_id, name, *args, **kwargs)


    def set_g2g_input(self, product_id, key: str, value: float):
        # store either on the product or in a company dict
        prod = self.get_product(product_id)  # adapt to your API
        prod.g2g_inputs[key] = float(value)

    def compute_g2g_label(self, product_id, *, calc, fp_db, year, region_id: str):
        prod = self.get_product(product_id)
        score, fu_total = calc.compute_g2g_score(
            product=prod,
            fp_db=fp_db,
            year=year,
            region_id=region_id,
            producer_params=getattr(prod, "g2g_inputs", {}),
            store_result=True,
        )
        return score, fu_total


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
    #intensity_key: str = ""

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


# returns dict[intensity_key -> qty], e.g. {"electricity_kwh": 18.0, ...}
# (you can also return FU separately later)



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
    market_key_map: Dict[str, str] = field(default_factory=dict)

    # Rule-level default parameters (used by FlowSpecs if scalar_param_name references these)
    rule_params: Dict[str, float] = field(default_factory=dict)

    # Flow specs
    flows: List[FlowSpec] = field(default_factory=list)

    # Rule-specific calculation hook
    compute_fn: Optional[ComputeFlowsFn] = None

    def compute_flows(
            self,
            producer_params: Mapping[str, float],
    ) -> tuple[Dict[str, float], Optional[float]]:
        if self.compute_fn is None:
            raise NotImplementedError(
                f"RuleSet '{self.rule_set_id}' has no compute_fn. "
                "Provide a rule-specific compute function."
            )
        return self.compute_fn(self, producer_params)

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

#ImpactVector = Dict[ImpactID, float] #Not needed, prototype is simplified to one impact


@dataclass
class GateToGraveCalculator:
    """
    Region-average G2G calculator (prototype, single indicator):
    - Reads cradle-to-gate score from FootprintDatabase (CO2 only)
    - Adds post-production contributions valued via AggregatedMarketDatabaseLike
    - Returns (total_score, functional_output_total)
    """

    rule_catalog: RuleCatalog
    market_db: AggregatedMarketDatabaseLike
    class_db: Optional[ClassificationDatabase] = None

    def compute_g2g_score(
        self,
        product: G2GProduct,
        fp_db: FootprintDatabase,
        year: int,
        region_id: str,
        producer_params: Optional[Mapping[str, float]] = None,
        store_result: bool = False,
    ) -> tuple[float, Optional[float]]:

        producer_params = producer_params or {}

        ruleset = self.rule_catalog.resolve_ruleset(product.class_code)
        ruleset.assert_region_supported(region_id)

        # 1) Cradle-to-gate baseline (single indicator in base DB)
        pid = int(product.product_id)
        total_score = float(fp_db.get_footprint(pid))

        # 2) Compute semantic flows + optional functional output total
        semantic_flows, fu_total = ruleset.compute_flows(producer_params=producer_params)

        # 3) Value flows using market intensities (still uses impact_id arg, but we pass "CO2")
        IMPACT_ID = "CO2"
        for flow_name, qty in semantic_flows.items():
            if flow_name not in ruleset.market_key_map:
                raise KeyError(
                    f"RuleSet '{ruleset.rule_set_id}' missing market_key_map for flow '{flow_name}'."
                )
            market_key = ruleset.market_key_map[flow_name]
            intensity = float(self.market_db.get_intensity(market_key, region_id, IMPACT_ID, int(year)))
            total_score += float(qty) * intensity

        if store_result:
            fp_db.report(pid, float(total_score))

        return float(total_score), fu_total


# --------------------------------------------------------------------------------------
# Helpers: dummy rule inspired by a PEFCR-like structure (for prototyping)
# --------------------------------------------------------------------------------------

def _get_param(
    ruleset: RuleSet,
    flow_id: str,
    producer_params: Mapping[str, float],
) -> float:
    for f in ruleset.flows:
        if f.flow_id == flow_id:
            return f.resolve_quantity(
                producer_params=producer_params,
                rule_params=ruleset.rule_params,
            )
    raise KeyError(f"Missing FlowSpec '{flow_id}' in RuleSet '{ruleset.rule_set_id}'.")

def tshirt_compute_flows(
    ruleset: RuleSet,
    producer_params: Mapping[str, float],
) -> tuple[Dict[str, float], float]:

    lifetime_uses = _get_param(ruleset, "lifetime_uses", producer_params)
    uses_between_wash = _get_param(ruleset, "uses_between_wash", producer_params)
    detergent_per_wash = _get_param(ruleset, "detergent_per_wash_kg", producer_params)
    kwh_per_wash = _get_param(ruleset, "kwh_per_wash", producer_params)
    retail_per_item = _get_param(ruleset, "retail_per_item", producer_params)
    mass_kg = _get_param(ruleset, "mass_kg", producer_params)

    if uses_between_wash <= 0:
        raise ValueError("uses_between_wash must be > 0.")
    if lifetime_uses <= 0:
        raise ValueError("lifetime_uses must be > 0.")

    wash_count = lifetime_uses / uses_between_wash

    flows = {
        "retail_stage": retail_per_item,
        "detergent_use": detergent_per_wash * wash_count,
        "electricity_use": kwh_per_wash * wash_count,
        "eol_treatment": mass_kg,
    }

    functional_output_total = lifetime_uses
    return flows, functional_output_total


def build_illustrative_tshirt_ruleset(rule_set_id: str = "rule_tshirt_v0") -> RuleSet:
    return RuleSet(
        rule_set_id=rule_set_id,
        name="Illustrative T-shirt rule (PEFCR-inspired)",
        version="0.2",
        valid_regions=["EU", "CA"],
        flows=[
            FlowSpec("lifetime_uses", Stage.USE, "use", ParamSource.RULE_DEFAULT, default_quantity=100.0),
            FlowSpec("uses_between_wash", Stage.USE, "use/wash", ParamSource.RULE_DEFAULT, default_quantity=3.0),
            FlowSpec("detergent_per_wash_kg", Stage.USE, "kg/wash", ParamSource.RULE_DEFAULT, default_quantity=0.002),
            FlowSpec("kwh_per_wash", Stage.USE, "kWh/wash", ParamSource.RULE_DEFAULT, default_quantity=0.6),
            FlowSpec("retail_per_item", Stage.RETAIL, "item", ParamSource.RULE_DEFAULT, default_quantity=1.0),
            FlowSpec(
                "mass_kg", Stage.EOL, "kg",
                ParamSource.PRODUCER_REQUIRED,
                default_quantity=0.2,
                producer_param_name="mass_kg",
            ),
        ],
        compute_fn=tshirt_compute_flows,
        market_key_map={
            "retail_stage": "MK_RETAIL_AVG_PER_ITEM",
            "detergent_use": "MK_DETERGENT_KG",
            "electricity_use": "MK_ELEC_KWH",
            "eol_treatment": "MK_TEXTILE_EOL_KG",
        },
    )


def build_region_average_rule_catalog() -> RuleCatalog:
    """
    Creates a RuleCatalog with one dummy apparel rule and a mapping for HS-like code "6109".
    Adjust to match how you store HS codes (e.g., "6109" or "HS 6109").
    """
    catalog = RuleCatalog()
    tshirt = build_illustrative_tshirt_ruleset()
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
#     market.set_intensity("MK_RETAIL_AVG_PER_ITEM", "EU", "GWP100", 2026, 0.5)
#     market.set_intensity("MK_DETERGENT_KG", "EU", "GWP100", 2026, 2.0)
#     market.set_intensity("MK_ELEC_KWH", "EU", "GWP100", 2026, 0.25)
#     market.set_intensity("MK_TEXTILE_EOL_KG", "EU", "GWP100", 2026, 1.2)
#
#     calc = GateToGraveCalculator(rule_catalog=catalog, market_db=market)
#     # You would pass your real Product + FootprintRepository instances here.
