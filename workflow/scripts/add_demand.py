"""
Adds demand to the network.

Depending on study, the load will all be aggregated to a single load
type, or distributed to different sectors and end use fuels.
"""

import logging
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import pypsa
from _helpers import configure_logging, get_multiindex_snapshots, mock_snakemake
from constants_sector import (
    TRANSPORT_FUELS,
    SecCarriers,
    SecNames,
)

logger = logging.getLogger(__name__)


HOURS_PER_YEAR = 8760


def add_datacenter_demand(n: pypsa.Network, dc_config: dict, planning_horizons: list):
    """
    Add data center demand to the network as constant loads.

    Data centers are modeled as flat (constant) demand profiles. Demand values
    are selected per investment period from a wide-format CSV (rows = balancing
    areas, columns = years). If a planning horizon year exceeds the last
    available year in the file, the last available year's demand is used.

    Unit conversion
    ---------------
    The input CSV contains annual energy demand in MWh/year. These are
    converted to average power in MW by dividing by HOURS_PER_YEAR (8760),
    consistent with a flat load profile held constant across all snapshots.
    """
    if not dc_config.get("enable", False):
        return

    demand_file = dc_config.get("demand_file")
    if not demand_file:
        logger.warning("Data center demand enabled but no demand_file specified")
        return

    # Read wide-format CSV: index = balancing area (rb), columns = years (MWh/year)
    # Convert MWh/year -> MW by dividing by hours per year (flat load assumption)
    dc_demand = pd.read_csv(demand_file, index_col=0)
    dc_demand.columns = dc_demand.columns.astype(int)
    dc_demand = dc_demand.groupby(dc_demand.index).sum()  # aggregate duplicate zone entries
    dc_demand = dc_demand / HOURS_PER_YEAR  # MWh/year -> MW
    available_years = sorted(dc_demand.columns.tolist())

    def get_demand_year(horizon_year):
        """Return the latest available year that does not exceed horizon_year."""
        eligible = [y for y in available_years if y <= horizon_year]
        return max(eligible) if eligible else available_years[0]

    is_multiindex = isinstance(n.snapshots, pd.MultiIndex)
    buses_added = 0

    # Detect index format: ReEDS zone ("p1", "p10") or county GEOID ("p06085")
    # County GEOIDs are stored as "p" + 5-digit FIPS code (e.g. "p06085")
    # ReEDS zones are "p" + 1-3 digit number (e.g. "p1", "p134")
    sample_idx = str(dc_demand.index[0])
    use_county = sample_idx.startswith("p") and len(sample_idx) == 6 and sample_idx[1:].isdigit()

    if use_county:
        # County GEOID mode — demand file indexed by 5-digit FIPS codes
        if "county" not in n.buses.columns:
            logger.warning(
                "Bus attribute 'county' not found; cannot map county DC demand to buses"
            )
            return
        zone_to_buses = n.buses.groupby("county").groups
    else:
        # ReEDS zone mode — demand file indexed by zone IDs like "p1", "p10"
        if "reeds_zone" not in n.buses.columns:
            logger.warning(
                "Bus attribute 'reeds_zone' not found; cannot map DC demand to buses"
            )
            return
        zone_to_buses = n.buses.groupby("reeds_zone").groups

    flex_fraction = dc_config.get("flexibility_fraction", 0)

    if is_multiindex:
        inv_periods = n.snapshots.get_level_values(0)
        unique_periods = inv_periods.unique()

    # Lazy-loaded resources for nearest-bus fallback (county mode only).
    _county_gdf = None
    _bus_kdtree = None
    _bus_index = None

    def _nearest_bus_for_missing_county(fips_tag: str):
        """Return the single nearest bus for a county FIPS tag not in zone_to_buses."""
        nonlocal _county_gdf, _bus_kdtree, _bus_index
        from scipy.spatial import cKDTree

        if _bus_kdtree is None:
            bus_xy = n.buses[["x", "y"]].values
            _bus_kdtree = cKDTree(bus_xy)
            _bus_index = n.buses.index.tolist()

        if _county_gdf is None:
            shp = Path(__file__).parent.parent / "data/counties/cb_2020_us_county_500k.shp"
            if not shp.exists():
                return None
            import geopandas as gpd
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                _county_gdf = gpd.read_file(shp)

        fips_5 = fips_tag[1:] if fips_tag.startswith("p") else fips_tag
        row = _county_gdf[_county_gdf["GEOID"] == fips_5]
        if row.empty:
            return None
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            centroid = row.geometry.centroid.iloc[0]
        _, idx = _bus_kdtree.query([centroid.x, centroid.y])
        return _bus_index[idx]

    # Add ONE load per ReEDS zone assigned to the highest-Pd bus in the zone.
    # The base network has ~82K buses; distributing to all buses within each zone
    # would create ~78K loads with 26K-snapshot time series (2 billion values).
    # Since clustering aggregates all intra-zone buses into a single clustered bus,
    # intra-zone placement does not affect optimization results.
    load_p_set_cols = {}       # load_name -> Series
    flex_p_nom = {}            # gen_name -> float
    flex_p_max_pu_cols = {}    # gen_name -> Series (only when flex_fraction > 0)

    for rb in dc_demand.index:
        if rb not in zone_to_buses:
            if use_county:
                nearest = _nearest_bus_for_missing_county(rb)
                if nearest is None:
                    logger.warning(f"County {rb!r} not in network and no shapefile fallback — skipping")
                    continue
                logger.warning(
                    f"County {rb!r} not in network — assigning demand to nearest bus {nearest!r}"
                )
                zone_buses = [nearest]
            else:
                logger.warning(f"Data center zone {rb!r} not found in network buses, skipping")
                continue
        else:
            zone_buses = zone_to_buses[rb]

        # Assign full zone demand to the single bus with highest Pd in the zone.
        bus_loads = n.buses.loc[zone_buses, "Pd"].clip(lower=0)
        bus = bus_loads.idxmax() if bus_loads.sum() > 0 else zone_buses[0]

        # Precompute per-period demand for this zone (only 3 unique periods, not 26280 snapshots)
        if is_multiindex:
            period_to_zone_demand = {
                p: dc_demand.loc[rb, get_demand_year(p)] for p in unique_periods
            }
            p_set = pd.Series(inv_periods.map(period_to_zone_demand).values, index=n.snapshots, dtype=float)
        else:
            year = get_demand_year(planning_horizons[0]) if planning_horizons else available_years[0]
            p_set = pd.Series(dc_demand.loc[rb, year], index=n.snapshots, dtype=float)

        load_p_set_cols[f"{bus} DC"] = p_set
        buses_added += 1

        # Add paired flexibility generator when flexibility is enabled.
        # This represents voluntarily unserved DC demand (e.g. deferred workloads).
        # p_nom is set to the maximum rated capacity across all periods; p_max_pu
        # varies per snapshot so the per-hour cap tracks the current period's
        # rated capacity. The annual energy cap is enforced separately via
        # add_dc_flexibility_constraint() in opts/policy.py.
        if flex_fraction > 0:
            if is_multiindex:
                p_nom = max(period_to_zone_demand.values())
                if p_nom > 0:
                    pu_map = {p: v / p_nom for p, v in period_to_zone_demand.items()}
                    p_max_pu = pd.Series(inv_periods.map(pu_map).values, index=n.snapshots, dtype=float)
                else:
                    p_max_pu = pd.Series(0.0, index=n.snapshots, dtype=float)
            else:
                p_nom = float(p_set.iloc[0])
                p_max_pu = 1.0

            if p_nom > 0:
                flex_p_nom[f"{bus} dc_flexibility"] = p_nom
                flex_p_max_pu_cols[f"{bus} dc_flexibility"] = p_max_pu

    # Single batched madd for all DC loads — avoids O(n²) per-call DataFrame rebuilds
    if load_p_set_cols:
        load_names = list(load_p_set_cols.keys())
        load_buses = [name.removesuffix(" DC") for name in load_names]
        p_set_df = pd.DataFrame(load_p_set_cols, index=n.snapshots)
        n.madd("Load", load_names, bus=load_buses, p_set=p_set_df, carrier="DC")

    if flex_p_nom:
        gen_names = list(flex_p_nom.keys())
        gen_buses = [name.removesuffix(" dc_flexibility") for name in gen_names]
        p_max_pu_df = pd.DataFrame(flex_p_max_pu_cols, index=n.snapshots) if is_multiindex else 1.0
        n.madd(
            "Generator",
            gen_names,
            bus=gen_buses,
            p_nom=pd.Series(flex_p_nom),
            p_max_pu=p_max_pu_df,
            p_min_pu=0,
            marginal_cost=0,
            carrier="dc_flexibility",
        )

    # Add carriers if not already present
    if "DC" not in n.carriers.index:
        n.add("Carrier", "DC", nice_name="Data Center", color="#7f7f7f")
    flex_fraction = dc_config.get("flexibility_fraction", 0)
    if flex_fraction > 0 and "dc_flexibility" not in n.carriers.index:
        n.add("Carrier", "dc_flexibility", nice_name="Data Center Flexibility", color="#aec7e8")

    logger.info(
        f"Added data center demand across {buses_added} buses "
        f"(converted from MWh/year to MW using {HOURS_PER_YEAR} hours/year)"
        + (f" with {flex_fraction:.0%} flexibility option" if flex_fraction > 0 else "")
    )


def attach_demand(n: pypsa.Network, df: pd.DataFrame, carrier: str, suffix: str):
    """
    Add demand to network from specified configuration setting.

    Returns network with demand added.
    """
    df.index = pd.to_datetime(df.index)
    assert len(df.index) == len(
        n.snapshots,
    ), "Demand time series length does not match network snapshots"
    df.index = n.snapshots
    n.madd(
        "Load",
        df.columns,
        suffix=suffix,
        bus=df.columns,
        p_set=df,
        carrier=carrier,
    )


if __name__ == "__main__":
    if "snakemake" not in globals():
        snakemake = mock_snakemake("add_demand", interconnect="western")
    configure_logging(snakemake)

    demand_files = snakemake.input.demand
    n = pypsa.Network(snakemake.input.network)

    sectors = snakemake.params.sectors

    # add snapshots
    sns_config = snakemake.params.snapshots
    planning_horizons = snakemake.params.planning_horizons

    n.snapshots = get_multiindex_snapshots(sns_config, planning_horizons)
    n.set_investment_periods(periods=planning_horizons)

    if isinstance(demand_files, str):
        demand_files = [demand_files]

    if sectors == "E" or sectors == "":  # electricity only
        assert len(demand_files) == 1

        suffix = ""
        carrier = "AC"

        df = pd.read_csv(demand_files[0], index_col=0)
        attach_demand(n, df, carrier, suffix)
        logger.info("Electricity demand added to network")

    else:  # sector files
        for demand_file in demand_files:
            parsed_name = Path(demand_file).name.split("_")
            parsed_name[-1] = parsed_name[-1].split(".pkl")[0]

            if len(parsed_name) == 2:
                sector = parsed_name[0].upper()
                end_use = parsed_name[1].upper().replace("-", "_")

                sec_name = SecNames[sector].value
                if sector.lower() == "transport":  # hack for now to get names to work
                    sec_car = TRANSPORT_FUELS[end_use.lower()]
                else:
                    sec_car = SecCarriers[end_use].value

                carrier = f"{sec_name}-{sec_car}"

                log_statement = f"{sector} {end_use} demand added to network"

            else:
                raise NotImplementedError

            suffix = f"-{carrier}"

            df = pd.read_pickle(demand_file)
            attach_demand(n, df, carrier, suffix)
            logger.info(log_statement)

    # Add data center demand if configured
    dc_config = snakemake.config.get("data_centers", {})
    add_datacenter_demand(n, dc_config, planning_horizons)

    n.export_to_netcdf(snakemake.output.network)
