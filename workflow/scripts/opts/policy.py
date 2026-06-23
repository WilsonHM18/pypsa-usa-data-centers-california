import logging  # noqa: D100

import numpy as np
import pandas as pd
from opts._helpers import (
    ceil_precision,
    filter_components,
    floor_precision,
    get_model_horizon,
    get_region_buses,
)
from pypsa.descriptors import get_switchable_as_dense as get_as_dense

logger = logging.getLogger(__name__)


def add_technology_capacity_target_constraints(n, config):
    """
    Add Technology Capacity Target (TCT) constraint to the network.

    Add minimum or maximum levels of generator nominal capacity per carrier for individual regions.
    Each constraint can be designated for a specified planning horizon in multi-period models.
    Opts and path for technology_capacity_targets.csv must be defined in config.yaml.
    Default file is available at config/policy_constraints/technology_capacity_targets.csv.

    Parameters
    ----------
    n : pypsa.Network
    config : dict

    Example
    -------
    scenario:
        opts: [Co2L-TCT-24H]
    electricity:
        technology_capacity_target: config/policy_constraints/technology_capacity_target.csv
    """
    tct_data = pd.read_csv(config["electricity"]["technology_capacity_targets"])
    if tct_data.empty:
        return

    model_horizon = get_model_horizon(n.model)

    for _, target in tct_data.iterrows():
        planning_horizon = target.planning_horizon
        if planning_horizon != "all" and int(planning_horizon) > max(model_horizon):
            continue

        region_list = [region_.strip() for region_ in target.region.split(",")]
        carrier_list = [carrier_.strip() for carrier_ in target.carrier.split(",")]
        region_buses = get_region_buses(n, region_list)

        lhs_gens_ext = filter_components(
            n=n,
            component_type="Generator",
            planning_horizon=planning_horizon,
            carrier_list=carrier_list,
            region_buses=region_buses.index,
            extendable=True,
        )
        lhs_gens_existing = filter_components(
            n=n,
            component_type="Generator",
            planning_horizon=planning_horizon,
            carrier_list=carrier_list,
            region_buses=region_buses.index,
            extendable=False,
        )

        lhs_storage_ext = filter_components(
            n=n,
            component_type="StorageUnit",
            planning_horizon=planning_horizon,
            carrier_list=carrier_list,
            region_buses=region_buses.index,
            extendable=True,
        )
        lhs_storage_existing = filter_components(
            n=n,
            component_type="StorageUnit",
            planning_horizon=planning_horizon,
            carrier_list=carrier_list,
            region_buses=region_buses.index,
            extendable=False,
        )

        lhs_link_ext = filter_components(
            n=n,
            component_type="Link",
            planning_horizon=planning_horizon,
            carrier_list=carrier_list,
            region_buses=region_buses.index,
            extendable=True,
        )
        lhs_link_existing = filter_components(
            n=n,
            component_type="Link",
            planning_horizon=planning_horizon,
            carrier_list=carrier_list,
            region_buses=region_buses.index,
            extendable=False,
        )

        if region_buses.empty or (lhs_gens_ext.empty and lhs_storage_ext.empty and lhs_link_ext.empty):
            continue

        if not lhs_gens_ext.empty:
            grouper_g = pd.concat(
                [lhs_gens_ext.bus.map(n.buses.country), lhs_gens_ext.carrier],
                axis=1,
            ).rename_axis(
                "Generator-ext",
            )
            lhs_g = n.model["Generator-p_nom"].loc[lhs_gens_ext.index].groupby(grouper_g).sum().rename(bus="country")
        else:
            lhs_g = None

        if not lhs_storage_ext.empty:
            grouper_s = pd.concat(
                [lhs_storage_ext.bus.map(n.buses.country), lhs_storage_ext.carrier],
                axis=1,
            ).rename_axis(
                "StorageUnit-ext",
            )
            lhs_s = n.model["StorageUnit-p_nom"].loc[lhs_storage_ext.index].groupby(grouper_s).sum()
        else:
            lhs_s = None

        if not lhs_link_ext.empty:
            grouper_l = pd.concat(
                [lhs_link_ext.bus.map(n.buses.country), lhs_link_ext.carrier],
                axis=1,
            ).rename_axis(
                "Link-ext",
            )
            lhs_l = n.model["Link-p_nom"].loc[lhs_link_ext.index].groupby(grouper_l).sum()
        else:
            lhs_l = None

        if lhs_g is None and lhs_s is None and lhs_l is None:
            continue
        else:
            gen = lhs_g.sum() if lhs_g else 0
            lnk = lhs_l.sum() if lhs_l else 0
            sto = lhs_s.sum() if lhs_s else 0

        lhs = gen + lnk + sto

        lhs_existing = lhs_gens_existing.p_nom.sum() + lhs_storage_existing.p_nom.sum() + lhs_link_existing.p_nom.sum()

        if target["max"] == "existing":
            target["max"] = ceil_precision(lhs_existing, 2)
        else:
            target["max"] = float(target["max"])

        if target["min"] == "existing":
            target["min"] = floor_precision(lhs_existing, 2)
        else:
            target["min"] = float(target["min"])

        if not np.isnan(target["min"]):
            rhs = floor_precision(target["min"] - lhs_existing, 2)

            n.model.add_constraints(
                lhs >= rhs,
                name=f"GlobalConstraint-{target.name}_{target.planning_horizon}_min",
            )

            logger.info(
                f"Adding TCT Constraint: Name: {target.name}, Planning Horizon: {target.planning_horizon}, Region: {target.region}, Carrier: {target.carrier}, Min Value: {target['min']}, Min Value Adj: {rhs}",
            )

        if not np.isnan(target["max"]):
            assert target["max"] >= lhs_existing, (
                f"TCT constraint of {target['max']} MW for {target['carrier']} must be at least {lhs_existing}"
            )

            rhs = ceil_precision(target["max"] - lhs_existing, 2)

            n.model.add_constraints(
                lhs <= rhs,
                name=f"GlobalConstraint-{target.name}_{target.planning_horizon}_max",
            )

            logger.info(
                f"Adding TCT Constraint: Name: {target.name}, Planning Horizon: {target.planning_horizon}, Region: {target.region}, Carrier: {target.carrier}, Max Value: {target['max']}, Max Value Adj: {rhs}",
            )


def add_RPS_constraints(n, config, sector, snakemake=None):
    """
    Add Renewable Portfolio Standards (RPS) constraints to the network.

    This function enforces constraints on the percentage of electricity generation
    from renewable energy sources for specific regions and planning horizons.
    It reads the necessary data from configuration files and the network.

    The differenct between electrical and sector implementation is:
    - Electrical applies RPS against exogenously defined demand
    - Sector applies RPS against endogenously solved power sector generation

    Parameters
    ----------
    n : pypsa.Network
        The PyPSA network object.
    config : dict
        A dictionary containing configuration settings and file paths.
    sector: bool
        Sector study
    snakemake: object, optional
        Snakemake object containing inputs and parameters

    Returns
    -------
    None
    """

    def process_reeds_data(filepath, carriers, value_col):
        """Helper function to process RPS or CES REEDS data."""
        reeds = pd.read_csv(filepath)

        # Handle both wide and long formats
        if "rps_all" not in reeds.columns:
            reeds = reeds.melt(
                id_vars="st",
                var_name="planning_horizon",
                value_name=value_col,
            )

        # Standardize column names
        reeds = reeds.rename(
            columns={"st": "region", "t": "planning_horizon", "rps_all": "pct"},
        )
        reeds["carrier"] = [", ".join(carriers)] * len(reeds)

        # Ensure the final dataframe has consistent columns
        reeds = reeds[["region", "planning_horizon", "carrier", "pct"]]
        reeds = reeds[reeds["pct"] > 0.0]  # Remove any rows with zero or negative percentages

        return reeds

    # Get model horizon
    model_horizon = get_model_horizon(n.model)

    # Read portfolio standards data
    portfolio_standards = pd.read_csv(config["electricity"]["portfolio_standards"])

    # Define carriers for RPS and CES
    rps_carriers = [
        "onwind",
        "offwind",
        "offwind_floating",
        "solar",
        "hydro",
        "geothermal",
        "biomass",
        "EGS",
    ]
    ces_carriers = [*rps_carriers, "nuclear", "SMR", "hydrogen_ct", "CCGT-95CCS", "CCGT-99CCS", "Coal-95CCS"]

    # Process RPS and CES REEDS data
    rps_reeds = process_reeds_data(
        snakemake.input.rps_reeds,
        rps_carriers,
        value_col="pct",
    )
    ces_reeds = process_reeds_data(
        snakemake.input.ces_reeds,
        ces_carriers,
        value_col="pct",
    )

    # Explicit portfolio_standards.csv entries take precedence over REEDS data.
    # Filter REEDS rows for any (region, planning_horizon) already covered explicitly
    # to avoid duplicate constraint names when both sources map to the same trading zone.
    explicit_pairs = set(
        zip(
            portfolio_standards.region.astype(str).str.strip(),
            portfolio_standards.planning_horizon.astype(int),
        )
    )
    rps_reeds = rps_reeds[
        ~rps_reeds.apply(
            lambda r: (str(r.region).strip(), int(r.planning_horizon)) in explicit_pairs,
            axis=1,
        )
    ]
    ces_reeds = ces_reeds[
        ~ces_reeds.apply(
            lambda r: (str(r.region).strip(), int(r.planning_horizon)) in explicit_pairs,
            axis=1,
        )
    ]

    # Concatenate all portfolio standards
    portfolio_standards = pd.concat([portfolio_standards, rps_reeds, ces_reeds])

    portfolio_standards = portfolio_standards[
        (portfolio_standards.pct > 0.0)
        & (
            portfolio_standards.planning_horizon.isin(
                snakemake.params.planning_horizons,
            )
        )
        & (portfolio_standards.region.isin(n.buses.reeds_state.unique()))
    ]

    mapper = n.buses.groupby("reeds_state")["rec_trading_zone"].first().to_dict()
    portfolio_standards["rec_trading_zone"] = portfolio_standards.region.map(mapper).fillna(portfolio_standards.region)

    for _, constraint_row in portfolio_standards.iterrows():
        region_list = [region.strip() for region in constraint_row.region.split(",")]
        region_buses = get_region_buses(n, region_list)
        if region_buses.empty:
            continue

        region_demand = (
            n.loads_t.p_set.loc[constraint_row.planning_horizon]
            .loc[:, n.loads.bus.isin(region_buses.index)]
            .sum()
            .sum()
        )
        region_rps_rhs = int(constraint_row.pct * region_demand)
        portfolio_standards.loc[constraint_row.name, "rps_rhs"] = region_rps_rhs

        if sector:
            # power level buses
            pwr_buses = n.buses[(n.buses.carrier == "AC") & (n.buses.index.isin(region_buses.index))]
            # links delievering power within the region
            # removes any transmission links
            pwr_links = n.links[(n.links.bus0.isin(pwr_buses.index)) & ~(n.links.bus1.isin(pwr_buses.index))]
            region_demand_sector = (
                n.model["Link-p"].sel(period=constraint_row.planning_horizon, Link=pwr_links.index).sum()
            )
            region_rps_rhs_sector = int(constraint_row.pct * region_demand_sector)
            portfolio_standards.loc[constraint_row.name, "rps_rhs_sector"] = region_rps_rhs_sector

    # Iterate through constraints and add RPS constraints to the model
    for (rec_trading_zone, planning_horizon, policy_carriers), zone_constraints in portfolio_standards.groupby(
        ["rec_trading_zone", "planning_horizon", "carrier"],
    ):
        if planning_horizon not in model_horizon:
            continue
        region_buses = get_region_buses(n, zone_constraints.region.unique())
        carriers = [carrier.strip() for carrier in policy_carriers.split(",")]

        # Filter region generators
        region_gens = n.generators[n.generators.bus.isin(region_buses.index)]
        region_gens_eligible = region_gens[region_gens.carrier.isin(carriers)]

        if region_gens_eligible.empty:
            return

        elif not sector:
            # Eligible generation
            p_eligible = n.model["Generator-p"].sel(
                period=planning_horizon,
                Generator=region_gens_eligible.index,
            )
            renewable_gen = zone_constraints.rps_rhs.sum()
            lhs = p_eligible.sum() - renewable_gen
            rhs = 0

        elif sector:
            # generator power contributing
            p_eligible = n.model["Generator-p"].sel(
                period=planning_horizon,
                Generator=region_gens_eligible.index,
            )
            renewable_gen = zone_constraints.rps_rhs_sector.sum()
            lhs = p_eligible.sum() - renewable_gen
            rhs = 0

        else:
            logger.error("Undefined control flow for RPS constraint.")

        n.model.add_constraints(
            lhs >= rhs,
            name=f"GlobalConstraint-{rec_trading_zone}_{planning_horizon}_rps_limit",
        )

        logger.info(
            f"Added RPS constraint '{rec_trading_zone}' for {planning_horizon} "
            f"requiring {renewable_gen / 1e6:.1f} TWh of {policy_carriers} generation ",
        )


def add_regional_co2limit(n, config):
    """Adding regional regional CO2 Limits Specified in the config.yaml."""
    model_horizon = get_model_horizon(n.model)
    regional_co2_lims = pd.read_csv(
        config["electricity"]["regional_Co2_limits"],
        index_col=[0],
    )

    regional_co2_lims = regional_co2_lims[regional_co2_lims.planning_horizon.isin(n.investment_periods)]
    weightings = n.snapshot_weightings.loc[n.snapshots]

    for idx, emmission_lim in regional_co2_lims.iterrows():
        region_list = [region.strip() for region in emmission_lim.regions.split(",")]
        region_buses = get_region_buses(n, region_list)

        emissions = n.carriers.co2_emissions.fillna(0)[lambda ds: ds != 0]
        region_gens = n.generators[n.generators.bus.isin(region_buses.index)]
        region_gens_em = region_gens.query("carrier in @emissions.index")

        if region_buses.empty or region_gens_em.empty:
            continue

        region_co2lim = emmission_lim.limit
        planning_horizon = emmission_lim.planning_horizon
        if planning_horizon not in model_horizon:
            continue

        efficiency = get_as_dense(
            n,
            "Generator",
            "efficiency",
            inds=region_gens_em.index,
        )  # mw_elect/mw_th
        em_pu = region_gens_em.carrier.map(emissions) / efficiency  # tonnes_co2/mw_electrical
        em_pu = em_pu.multiply(weightings.generators, axis=0).loc[planning_horizon].fillna(0)

        # Emitting Gens
        p_em = n.model["Generator-p"].loc[:, region_gens_em.index].sel(period=planning_horizon)

        # CO2 Atmospheric Emissions
        if any(n.carriers.index.isin(["co2"])):
            co2_atm = n.stores.loc[["atmosphere" in name for name in n.stores.index]]
            last_timestep = n.snapshots.get_level_values(1)[-1]
            end_co2_atm_storage = (
                n.model["Store-e"].loc[:, co2_atm.index].sel(period=planning_horizon).sel(timestep=last_timestep)
            ).sum()
        else:
            end_co2_atm_storage = 0

        lhs = (p_em * em_pu).sum() + end_co2_atm_storage
        rhs = region_co2lim

        n.model.add_constraints(
            lhs <= rhs,
            name=f"GlobalConstraint-{emmission_lim.name}_{planning_horizon}co2_limit",
        )

        logger.info(
            f"Adding regional Co2 Limit for {emmission_lim.name} in {planning_horizon} with limit {rhs}",
        )


def add_cfe_matching_constraint(n, config):
    """
    Enforce Carbon-Free Energy (CFE) matching for data center loads.

    Two modes are supported, selected via ``config["data_centers"]["cfe_matching"]``:

    * ``"24_7"``  — per-snapshot system-wide constraint: total CFE generation in
      every hour must be ≥ total DC load in that hour.  This mirrors corporate
      24/7 CFE pledges (e.g. Google, Microsoft).

    * ``"annual"`` — per-investment-period constraint: total weighted CFE energy
      over each planning period must be ≥ total weighted DC load energy.  This
      mirrors annual renewable energy certificate (REC) matching.

    CFE generators are identified dynamically as those whose carrier has zero
    CO2 emissions (``n.carriers.co2_emissions == 0``).
    """
    cfe_mode = config.get("data_centers", {}).get("cfe_matching")
    if not cfe_mode:
        return

    dc_loads = n.loads[n.loads.carrier == "DC"].index
    if dc_loads.empty:
        logger.info("No DC loads found; skipping CFE matching constraint.")
        return

    # Identify zero-emission (CFE) generators
    cfe_carriers = n.carriers[n.carriers.co2_emissions.fillna(0) == 0].index
    cfe_gens = n.generators[n.generators.carrier.isin(cfe_carriers)].index
    if cfe_gens.empty:
        logger.warning("No CFE generators found; skipping CFE matching constraint.")
        return

    is_multiindex = isinstance(n.snapshots, pd.MultiIndex)
    weightings = n.snapshot_weightings.loc[n.snapshots]
    model_horizon = get_model_horizon(n.model)

    # Precompute DC load series — use get_switchable_as_dense so static p_set loads
    # (not in n.loads_t.p_set) are handled correctly alongside time-varying ones.
    all_loads_p_set = n.get_switchable_as_dense("Load", "p_set")
    dc_load_series = all_loads_p_set[dc_loads].sum(axis=1)  # total DC load per snapshot

    if cfe_mode == "24_7":
        # Vectorized: one constraint per snapshot.
        # Total CFE generation (summed across all CFE generators) >= total DC load.
        # A slack variable absorbs structurally infeasible hours (e.g. nighttime in
        # periods where dispatchable clean baseload < DC load).  The slack is penalised
        # at $1 M/MWh so the optimiser only activates it when strictly necessary.
        import xarray as xr

        CFE_SLACK_PENALTY = 1_000_000  # $/MWh — large enough to minimise slack

        cfe_p = n.model["Generator-p"].sel({"Generator": cfe_gens})
        lhs = cfe_p.sum("Generator")  # DataArray indexed by snapshot

        rhs = xr.DataArray(dc_load_series.values, coords={"snapshot": n.snapshots}, dims="snapshot")

        # Slack variable: one per snapshot, non-negative
        cfe_slack = n.model.add_variables(
            lower=0,
            coords=[n.snapshots],
            name="cfe_247_slack",
        )
        n.model.add_constraints(lhs + cfe_slack >= rhs, name="cfe_247")

        # Add slack penalty to objective
        weightings_gen = n.snapshot_weightings.generators
        weights_da = xr.DataArray(
            weightings_gen.values, coords={"snapshot": n.snapshots}, dims="snapshot"
        )
        n.model.objective = n.model.objective + (cfe_slack * weights_da * CFE_SLACK_PENALTY).sum()

        logger.info(
            f"Added 24/7 CFE matching constraints with slack "
            f"({len(n.snapshots)} snapshots, {len(cfe_gens)} CFE generators, "
            f"{len(dc_loads)} DC loads, penalty=${CFE_SLACK_PENALTY:,}/MWh)"
        )

    elif cfe_mode == "annual":
        # One constraint per investment period: weighted CFE energy >= weighted DC energy
        periods_to_run = [p for p in n.investment_periods if p in model_horizon] if is_multiindex else [None]
        for period in periods_to_run:
            if is_multiindex:
                period_snaps = n.snapshots[n.snapshots.get_level_values(0) == period]
                weights = weightings.generators.loc[period]
            else:
                period_snaps = n.snapshots
                weights = weightings.generators

            cfe_p = n.model["Generator-p"].sel({"snapshot": period_snaps, "Generator": cfe_gens})
            import xarray as xr
            weights_da = xr.DataArray(weights.values, coords=[period_snaps], dims=["snapshot"])
            lhs = (cfe_p * weights_da).sum()
            dc_energy = float((dc_load_series.loc[period_snaps] * weights).sum())
            constraint_name = f"cfe_annual_{period}" if is_multiindex else "cfe_annual"
            n.model.add_constraints(lhs >= dc_energy, name=constraint_name)
        logger.info(
            f"Added annual CFE matching constraints (mode=annual, "
            f"{len(cfe_gens)} CFE generators)"
        )
    else:
        logger.warning(f"Unknown cfe_matching mode '{cfe_mode}'; skipping.")


def report_cfe_slack(n):
    """
    After solving, log CFE 24/7 slack usage. Called once per investment period.

    Reports number/fraction of hours where clean generation couldn't cover DC
    load, plus max/mean shortfall and total unmatched energy.
    """
    if "cfe_247_slack" not in n.model.solution:
        return

    slack_vals = n.model.solution["cfe_247_slack"].to_series()
    slack_vals = slack_vals[slack_vals.index.isin(n.snapshots)]

    threshold = 1e-3  # MW — treat values below this as numerically zero
    violated = slack_vals[slack_vals > threshold]
    n_total = len(slack_vals)
    n_violated = len(violated)

    if n_violated == 0:
        logger.info("CFE 24/7: no infeasible hours — slack unused.")
        return

    weights = n.snapshot_weightings.generators.reindex(violated.index, fill_value=1.0)
    slack_energy_mwh = float((violated * weights).sum())

    logger.warning(
        f"CFE 24/7 slack active: {n_violated}/{n_total} hours ({100*n_violated/n_total:.1f}%) "
        f"violated | max slack {violated.max():.1f} MW | mean {violated.mean():.1f} MW | "
        f"total unmatched energy {slack_energy_mwh:,.0f} MWh"
    )


def add_dc_flexibility_constraint(n, config):
    """
    Cap annual unserved data center energy per bus per investment period.

    For each bus that has a ``dc_flexibility`` generator, the total weighted
    dispatch over a planning period cannot exceed ``flexibility_fraction`` times
    the DC load energy in that same period.  The constraint is applied
    independently per bus so that regional flexibility is not pooled.

    Parameters
    ----------
    n : pypsa.Network
    config : dict
        Full Snakemake config dict; reads ``config["data_centers"]["flexibility_fraction"]``.
    """
    flex_fraction = config.get("data_centers", {}).get("flexibility_fraction", 0)
    if not flex_fraction:
        return

    flex_gens = n.generators[n.generators.carrier == "dc_flexibility"]
    if flex_gens.empty:
        logger.info("No dc_flexibility generators found; skipping constraint.")
        return

    is_multiindex = isinstance(n.snapshots, pd.MultiIndex)
    weightings = n.snapshot_weightings.loc[n.snapshots]
    model_horizon = get_model_horizon(n.model)

    n_constraints = 0
    all_loads_p_set = n.get_switchable_as_dense("Load", "p_set")

    if is_multiindex:
        for period in n.investment_periods:
            if period not in model_horizon:
                continue

            period_weights = weightings.generators.loc[period]

            for gen_name in flex_gens.index:
                bus = flex_gens.loc[gen_name, "bus"]

                dc_loads_at_bus = n.loads[
                    (n.loads.bus == bus) & (n.loads.carrier == "DC")
                ].index
                if dc_loads_at_bus.empty:
                    logger.warning(
                        f"No DC load at bus {bus}; skipping flexibility constraint."
                    )
                    continue
                dc_load_name = dc_loads_at_bus[0]

                # LHS: weighted flexibility dispatch this period
                flex_p = n.model["Generator-p"].loc[:, gen_name].sel(period=period)
                lhs = (flex_p * period_weights).sum()

                # RHS: fraction of DC load energy this period
                dc_energy = (
                    all_loads_p_set.loc[period][dc_load_name] * period_weights
                ).sum()
                rhs = float(flex_fraction * dc_energy)

                n.model.add_constraints(
                    lhs <= rhs,
                    name=f"dc_flexibility_{bus}_{period}",
                )
                n_constraints += 1
    else:
        weights = weightings.generators

        for gen_name in flex_gens.index:
            bus = flex_gens.loc[gen_name, "bus"]

            dc_loads_at_bus = n.loads[
                (n.loads.bus == bus) & (n.loads.carrier == "DC")
            ].index
            if dc_loads_at_bus.empty:
                logger.warning(
                    f"No DC load at bus {bus}; skipping flexibility constraint."
                )
                continue
            dc_load_name = dc_loads_at_bus[0]

            flex_p = n.model["Generator-p"].loc[:, gen_name]
            lhs = (flex_p * weights).sum()

            dc_energy = (all_loads_p_set[dc_load_name] * weights).sum()
            rhs = float(flex_fraction * dc_energy)

            n.model.add_constraints(lhs <= rhs, name=f"dc_flexibility_{bus}")
            n_constraints += 1

    logger.info(
        f"Added {n_constraints} DC flexibility constraints "
        f"(flexibility_fraction={flex_fraction:.0%})"
    )
