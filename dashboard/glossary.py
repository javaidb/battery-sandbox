"""Plain-language explanations for every metric the Electrochemistry view plots.

Each entry has three parts:
    what  - what the quantity is, physically
    moves - what changes it (operating conditions, cell design, ageing)
    look  - what to look for in the plot, and an experiment to try

Keys match the labels used in ``batterylab.echem`` (FIELDS and SERIES_GROUPS),
so ``tests/test_glossary.py`` can check that nothing plotted is unexplained.
"""

from __future__ import annotations

import streamlit as st

GLOSSARY: dict[str, dict[str, str]] = {
    # --- terminal --------------------------------------------------------------
    "Voltage [V]": {
        "title": "Terminal voltage",
        "what": "The voltage you would measure across the cell's tabs. It is the open-circuit voltage plus or minus every loss mechanism, "
                "so it is lower than OCV on discharge and higher on charge.",
        "moves": "State of charge (through the OCV), current (larger current, larger losses), temperature (cold raises every resistance), "
                 "and ageing (lost lithium or active material shrinks the usable window).",
        "look": "The sharp drop at the end of discharge is one electrode running out of lithium or space. Raise the C-rate: "
                "the whole curve shifts down and the cut-off arrives earlier, which is rate-limited capacity.",
    },
    "Current [A]": {
        "title": "Current",
        "what": "Current through the cell. Positive is charge, negative is discharge (the convention used throughout this repo).",
        "moves": "Set by the test protocol. In CC-CV charging the current tapers once the voltage limit is reached.",
        "look": "In CC-CV, the length of the taper tells you how far the cell was from equilibrium when it hit the voltage limit. "
                "It gets longer at higher C-rate and lower temperature.",
    },
    "Discharge capacity [Ah]": {
        "title": "Discharge capacity (charge moved)",
        "what": "Charge passed since the start of the test, counted positive on discharge.",
        "moves": "Current × time; nothing else.",
        "look": "Its value at cut-off is the capacity the cell delivered under these conditions. That depends on rate and temperature, "
                "not only on how much lithium the cell holds.",
    },
    "Cell temperature [°C]": {
        "title": "Cell temperature",
        "what": "Average cell temperature from a lumped thermal model: heat generated inside minus heat lost to the surroundings.",
        "moves": "Current squared (most losses scale with I²), internal resistance (higher when cold), entropic heat, and how well the cell is "
                 "cooled (the heat-transfer coefficient and heat capacity in the parameter set).",
        "look": "The temperature rise roughly quadruples when you double the C-rate. A cold cell warms itself during use, which lowers "
                "its resistance as it goes.",
    },
    # --- inside the cell (through-thickness fields) ----------------------------
    "Electrolyte concentration [mol/m³]": {
        "title": "Electrolyte concentration",
        "what": "Concentration of Li⁺ salt in the liquid that fills the pores and the separator. On discharge, Li⁺ is released in the anode "
                "and consumed in the cathode, so the salt piles up on the anode side and is depleted on the cathode side. "
                "On charge the slope reverses.",
        "moves": "Current (the gradient is proportional to it), electrolyte diffusivity (much lower when cold), transference number, "
                 "and electrode thickness, porosity and tortuosity (longer, more winding paths mean steeper gradients).",
        "look": "If it drops to near zero next to the cathode's current collector, the cell is limited by electrolyte transport and "
                "stops early. Try 3–5C. In a rest the profile flattens back to uniform within minutes.",
    },
    "Electrolyte potential [V]": {
        "title": "Electrolyte potential",
        "what": "Electric potential of the liquid phase, relative to a reference at the anode's current collector. Its slope drives "
                "the ionic current through the liquid.",
        "moves": "Current, ionic conductivity (lower when cold, and when the salt is depleted), tortuosity, and electrode/separator thickness.",
        "look": "The drop from one side of the cell to the other is the electrolyte's share of the voltage loss. It steepens at high C-rate "
                "and low temperature.",
    },
    "Particle surface stoichiometry": {
        "title": "Particle surface stoichiometry",
        "what": "Fraction of lithium sites filled at the surface of the active particles (0 = empty, 1 = full) at each point through the "
                "electrode. The surface value, not the average, sets the electrode's local potential.",
        "moves": "State of charge, solid diffusivity (strongly temperature-dependent), particle size (diffusion time scales with R²), "
                 "and how the reaction is spread across the electrode thickness.",
        "look": "Discharge ends when the anode surface empties or the cathode surface fills somewhere, even if the particle cores still "
                "have room. A profile that varies across x means the electrode is being used unevenly, usually more near the separator.",
    },
    "Interfacial current density [A/m²]": {
        "title": "Interfacial current density",
        "what": "Rate of lithium crossing the particle surface, per unit of particle surface area. Positive means lithium is leaving the "
                "solid (anode on discharge); negative means it is entering.",
        "moves": "Total current, and how it is shared out across the electrode. Electrolyte resistance pushes the reaction towards the "
                 "separator; a sloping OCP and fast kinetics even it out.",
        "look": "Look for a peak next to the separator at high C-rate. In LFP, with its flat OCP, you can see a reaction front sweep "
                "through the electrode. Uneven current means uneven ageing.",
    },
    "Reaction overpotential [V]": {
        "title": "Reaction overpotential",
        "what": "The extra potential needed to drive the charge-transfer reaction at the particle surface: η = φs − φe − U(surface). "
                "Butler–Volmer kinetics relates it to the local current, j ∝ i₀·sinh(F·η / 2RT).",
        "moves": "Local current, exchange-current density i₀ (rate constant, temperature through Arrhenius, and electrolyte and surface "
                 "concentrations), and active surface area (LAM removes area, raising η).",
        "look": "It grows sharply at low temperature and near an empty or full electrode, where i₀ falls towards zero. "
                "Try −10 °C at 1C.",
    },
    "Solid−electrolyte potential difference [V]": {
        "title": "Solid–electrolyte potential difference",
        "what": "Local potential of the electrode material relative to the electrolyte beside it (φs − φe). In the anode this is effectively "
                "the anode potential vs a Li/Li⁺ reference at that point. It equals the local OCP plus the reaction overpotential.",
        "moves": "Local stoichiometry (through the OCP) and overpotential, so current, temperature and state of charge.",
        "look": "In the anode during charge, watch the value next to the separator. If it drops below 0 V, lithium can plate as metal "
                "instead of intercalating. Fast and cold charging push it there.",
    },
    # --- particles -------------------------------------------------------------
    "particles": {
        "title": "Lithium inside a particle",
        "what": "Lithium fraction from the centre (r/R = 0) to the surface (r/R = 1) of an active particle. Lithium moves through the "
                "solid by diffusion, so a gradient means the surface is running ahead of the core.",
        "moves": "Solid diffusivity Ds (strongly temperature-dependent), particle radius R (time constant ≈ R²/Ds) and current. "
                 "In DFN, particles near the separator work harder than those near the current collector.",
        "look": "Steep profiles at high C or low temperature mean diffusion-limited capacity: the surface hits its limit while the "
                "core still has lithium to give. In a rest (GITT, Relaxation) the profile flattens, and the voltage relaxation you "
                "see at the terminals is this flattening.",
    },
    # --- voltage breakdown -----------------------------------------------------
    "Open-circuit voltage [V]": {
        "title": "Open-circuit voltage (OCV)",
        "what": "The voltage the cell would settle to if the current stopped and everything equilibrated: cathode OCP minus anode OCP, "
                "each at its average stoichiometry.",
        "moves": "State of charge and chemistry only (plus a little temperature). Ageing changes which part of each electrode's OCP "
                 "curve the cell uses.",
        "look": "Terminal voltage = OCV − all losses. The losses chart shows the gap.",
    },
    "Particle concentration overpotential [V]": {
        "title": "Particle (solid diffusion) loss",
        "what": "Voltage lost because the particle surfaces are more depleted or filled than their averages. It is the OCV computed from "
                "surface stoichiometries minus the OCV from average stoichiometries.",
        "moves": "Solid diffusivity (very temperature-sensitive), particle size, current, and the slope of the OCP curves.",
        "look": "It usually dominates at low temperature and towards the end of discharge, and builds up slowly. It is the source of "
                "the long voltage relaxation after a pulse, the part an RC branch with a long time constant tries to imitate.",
    },
    "Reaction overpotential [V]::loss": {
        "title": "Reaction (kinetic) loss",
        "what": "Voltage spent driving the charge-transfer reaction at particle surfaces, averaged through each electrode.",
        "moves": "Exchange-current density (rate constants, temperature via Arrhenius, concentrations), current, and active surface "
                 "area (lost to LAM).",
        "look": "It appears instantly when current flows and vanishes instantly when it stops. Together with the ohmic losses, it "
                "is what an ECM's R0 and fast RC branch capture.",
    },
    "Electrolyte concentration overpotential [V]": {
        "title": "Electrolyte concentration loss",
        "what": "A Nernst-type voltage from the salt-concentration gradient across the cell (the diffusion potential).",
        "moves": "Current, electrolyte diffusivity (temperature), transference number, and electrode thickness and tortuosity.",
        "look": "It builds over tens of seconds to minutes at a constant current and relaxes on the same scale. That gives a mid-range "
                "time constant in pulse tests.",
    },
    "Electrolyte ohmic loss [V]": {
        "title": "Electrolyte ohmic loss",
        "what": "I×R across the liquid: the resistance to ion flow through the pores and the separator.",
        "moves": "Ionic conductivity (falls steeply when cold, and when the salt is depleted), tortuosity, porosity and thickness.",
        "look": "Instantaneous: the step you see the moment a pulse starts. It is a large part of an ECM's R0.",
    },
    "Solid-phase ohmic loss [V]": {
        "title": "Solid-phase ohmic loss",
        "what": "I×R through the electronically conducting solid in each electrode.",
        "moves": "Electronic conductivity of the electrode material (with its carbon additive) and electrode thickness.",
        "look": "Usually tiny for graphite and NMC. Larger in poorly conducting materials such as LFP.",
    },
    # --- electrodes ------------------------------------------------------------
    "Anode average stoichiometry": {
        "title": "Anode average stoichiometry",
        "what": "Average lithium fraction across all anode particles: how full the anode is.",
        "moves": "Charge passed; the start and end values depend on cell balancing and on lithium inventory (LLI lowers them).",
        "look": "It moves in step with the cathode's in the opposite direction. The ratio of their movements is set by the electrodes' "
                "capacities, which LAM changes.",
    },
    "Cathode average stoichiometry": {
        "title": "Cathode average stoichiometry",
        "what": "Average lithium fraction across all cathode particles.",
        "moves": "Charge passed, cell balancing, and ageing (LAM_PE makes it swing faster per Ah).",
        "look": "Compare a fresh and an aged run (LAM_PE 10 %) in Any metric: the slope steepens.",
    },
    "Anode surface stoichiometry (x-avg)": {
        "title": "Anode surface stoichiometry (averaged through x)",
        "what": "Lithium fraction at the anode particle surfaces, averaged through the electrode thickness. This sets the anode's potential.",
        "moves": "Same as the average, plus solid diffusion: the surface leads the average when diffusion lags.",
        "look": "The gap between surface and average is the particle concentration loss, seen from the anode side.",
    },
    "Cathode surface stoichiometry (x-avg)": {
        "title": "Cathode surface stoichiometry (averaged through x)",
        "what": "Lithium fraction at the cathode particle surfaces, averaged through the electrode thickness.",
        "moves": "As for the anode. Cathode diffusivity is often the slower one in NMC cells.",
        "look": "At high C, the cathode surface reaching its upper limit is often what ends discharge.",
    },
    "Anode OCP [V]": {
        "title": "Anode open-circuit potential",
        "what": "Anode potential vs Li/Li⁺ at equilibrium for its current bulk stoichiometry. For graphite this is about 0.1–0.2 V over "
                "most of the range, with plateaus (staging).",
        "moves": "Anode stoichiometry and the material itself.",
        "look": "Graphite's flat plateaus mean small anode potential changes over large SoC changes. Combined with an LFP cathode, "
                "this makes the whole cell's OCV very flat, which makes voltage-based SoC estimation hard (module B2).",
    },
    "Cathode OCP [V]": {
        "title": "Cathode open-circuit potential",
        "what": "Cathode potential vs Li/Li⁺ at equilibrium for its current bulk stoichiometry.",
        "moves": "Cathode stoichiometry and the material: sloping for NMC/NCA/LCO, nearly flat at about 3.4 V for LFP.",
        "look": "The cell's OCV is cathode OCP minus anode OCP. Features in either one show up as peaks in dQ/dV (module A1).",
    },
    "windows": {
        "title": "Electrode windows",
        "what": "Each electrode's full OCP curve (grey) and the stretch this test actually used (coloured). The cell only ever uses part "
                "of each electrode; the balancing between them sets which part.",
        "moves": "Ageing. LLI slides the anode window towards lower lithium (less lithium to shuttle). LAM_PE or LAM_NE shrinks that "
                 "electrode's capacity, so the same Ah moves its window further. Rate and temperature shorten the windows "
                 "through the losses.",
        "look": "Pin a fresh run, set LLI = 10 % and compare. Recovering these shifts from measured curves is exactly what DVA/ICA "
                "fitting does in module A1.",
    },
    # --- heat ------------------------------------------------------------------
    "Ohmic heating [W]": {
        "title": "Ohmic heating",
        "what": "Joule heat from current flowing through resistance in the electrolyte and solids. Always ≥ 0.",
        "moves": "Current squared and the ohmic resistances (higher when cold).",
        "look": "It dominates at high C-rate.",
    },
    "Irreversible reaction heating [W]": {
        "title": "Irreversible reaction heating",
        "what": "Heat from driving the electrode reactions away from equilibrium (current × reaction overpotential). Always ≥ 0.",
        "moves": "Current and reaction overpotential, so temperature, kinetics and active surface area.",
        "look": "It rises sharply near the end of discharge, where overpotentials grow.",
    },
    "Reversible (entropic) heating [W]": {
        "title": "Reversible (entropic) heating",
        "what": "Heat from the entropy change of lithium entering or leaving the host lattice (j·T·dU/dT). Its sign depends on the "
                "direction of current and on state of charge.",
        "moves": "Current (linearly, not squared), temperature, and each electrode's entropic coefficient dU/dT across its stoichiometry.",
        "look": "It can be negative, so the cell cools while that part of the reaction runs. At low C-rates it can outweigh the "
                "irreversible terms.",
    },
    "Total heating [W]": {
        "title": "Total heating",
        "what": "Sum of all heat sources inside the cell.",
        "moves": "Everything above.",
        "look": "The cell temperature follows the balance between this and the cooling.",
    },
    # --- safety ----------------------------------------------------------------
    "Anode potential at separator vs Li [V]": {
        "title": "Anode potential at the separator",
        "what": "Anode solid–electrolyte potential difference (≈ potential vs Li/Li⁺) at the point of the anode closest to the cathode, "
                "where it is most negative during charge.",
        "moves": "Charging current, temperature (cold slows diffusion and kinetics), state of charge (lower near full), anode "
                 "thickness, and the anode/cathode capacity ratio.",
        "look": "Below the red 0 V line, lithium metal plating is thermodynamically possible. That is a safety and ageing risk. "
                "Compare a 0.5C charge at 25 °C with 3C at −10 °C.",
    },
}

# Voltage-breakdown labels reuse field names; map them to their loss entries.
LOSS_KEYS = {"Reaction overpotential [V]": "Reaction overpotential [V]::loss"}


def entry(key: str) -> dict[str, str]:
    return GLOSSARY.get(key, {"title": key, "what": "No explanation written yet.", "moves": "", "look": ""})


def card(key: str, color: str | None = None) -> None:
    """One compact explanation card; ``color`` adds a swatch matching the plotted series."""
    e = entry(key)
    with st.container(border=True):
        swatch = f"<span style='color:{color}'>■</span> " if color else ""
        st.markdown(f"{swatch}**{e['title']}**", unsafe_allow_html=True)
        st.caption(f"**What it is.** {e['what']}")
        if e["moves"]:
            st.caption(f"**What moves it.** {e['moves']}")
        if e["look"]:
            st.caption(f"**Look for.** {e['look']}")


def cards(keys: list[str], cols: int = 2, colors: list[str | None] | None = None) -> None:
    """Explanation cards in a grid, in the same order as the plots above them."""
    colors = list(colors or []) + [None] * len(keys)
    for i in range(0, len(keys), cols):
        row = st.columns(cols)
        for col, key, color in zip(row, keys[i : i + cols], colors[i : i + cols]):
            with col:
                card(key, color)
