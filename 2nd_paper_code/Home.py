# Home.py
import streamlit as st

st.set_page_config(page_title="SSELF — Prototype Launcher", layout="wide")
st.title("SSELF — Prototype Launcher")

st.write(
    "Select a prototype version. The first four tools demonstrate cradle-to-gate "
    "footprint calculation under different approaches to multifunctionality. "
    "The final tool extends cradle-to-gate results to a region-average cradle-to-grave footprint."
)

st.info(
    "Prototype note: these tools use simplified/dummy economies to demonstrate the SSELF architecture, "
    "data requirements, and calculation logic. They are not validated footprint calculators."
)

# ------------------------------------------------------------
# Cradle-to-gate section
# ------------------------------------------------------------
st.header("Cradle-to-gate prototypes")
st.caption(
    "These versions calculate product footprints up to the production gate. "
    "They differ mainly in how they handle multifunctionality and data granularity."
)

c1, c2 = st.columns(2)

with c1:
    st.subheader("📦 Basic cradle-to-gate simulation")
    st.caption(
        "Minimal SSELF configuration. Generates a random economy and calculates cradle-to-gate "
        "product footprints through iterative propagation. Multifunctionality is handled using "
        "company-wide economic allocation."
    )
    st.page_link(
        "pages/SSELF_base_app.py",
        label="Open basic cradle-to-gate simulation",
        icon="➡️",
    )

with c2:
    st.subheader("🌳 Cradle-to-gate with subdivision")
    st.caption(
        "Adds nested reporting units within companies, such as facilities, business units, "
        "or production lines. Impacts are first propagated through the internal hierarchy, "
        "then allocated economically within the relevant reporting unit."
    )
    st.page_link(
        "pages/SSELF_hierarchy_app.py",
        label="Open subdivision / hierarchy extension",
        icon="➡️",
    )

st.divider()

c3, c4 = st.columns(2)

with c3:
    st.subheader("⚖️ Cradle-to-gate with alternative allocation bases")
    st.caption(
        "Demonstrates how allocation can be based on non-monetary product properties, "
        "such as mass, volume, or energy, when those data are available. Economic allocation "
        "remains the universal fallback."
    )
    st.page_link(
        "pages/SSELF_allocation_app.py",
        label="Open allocation-basis extension",
        icon="➡️",
    )

with c4:
    st.subheader("🔁 Cradle-to-gate with system expansion and substitution")
    st.caption(
        "Demonstrates average substitution for multifunctional systems using product classification, "
        "function, production volumes, and market-average substitutes."
    )
    st.page_link(
        "pages/SSELF_substitution_app.py",
        label="Open system expansion / substitution extension",
        icon="➡️",
    )

# ------------------------------------------------------------
# Cradle-to-grave section
# ------------------------------------------------------------
st.divider()
st.header("Cradle-to-grave prototype")
st.caption(
    "This version starts from cradle-to-gate results and adds downstream estimates for retail, "
    "use phase, and end-of-life using rule-based assumptions."
)

c5, = st.columns(1)

with c5:
    st.subheader("🚚 Region-average cradle-to-grave extension")
    st.caption(
        "Extends cradle-to-gate product footprints with region-average downstream contributions. "
        "A product class resolves to a RuleSet, which combines producer inputs, default assumptions, "
        "and regional background data for retail, use, and end-of-life."
    )
    st.page_link(
        "pages/SSELF_G2G_region_app.py",
        label="Open region-average cradle-to-grave extension",
        icon="➡️",
    )