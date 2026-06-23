# PyPSA-USA: California Data Center Energy Study

This repository contains the code and configuration files to reproduce the California data center electricity demand scenarios from:

> **"The Grid Impacts of Data Center Growth in California"**
> Will McNeil, Steven Davis — Stanford University

The study uses [PyPSA-USA](https://pypsa-usa.readthedocs.io/en/latest/) to model the California bulk power system (58-node WECC network reduced to CA) across five policy scenarios examining how data center demand growth affects system costs, capacity investment, and emissions.

---

## Scenarios

| Scenario | Year | DC Demand | Mode | Config |
|---|---|---|---|---|
| Reference 2025 | 2025 | Current (2025) | Dispatch | `config.ca_ref2025.yaml` |
| Reference 2030 | 2030 | Projected (2030) | Dispatch | `config.ca_ref2030_ab32rps.yaml` |
| Counterfactual 2030 | 2030 | Frozen at 2025 | Dispatch | `config.ca_counterfactual2030_ab32rps.yaml` |
| Optimized 2030 | 2030 | Projected (2030) | Capacity expansion | `config.ca_optimized2030_ab32rps.yaml` |
| Counterfactual 2030 (Opt) | 2030 | Frozen at 2025 | Capacity expansion | `config.ca_counterfactual2030_opt_ab32rps.yaml` |

**Dispatch** scenarios fix generation capacity at the existing fleet and solve a production cost model.
**Capacity expansion** scenarios additionally optimize new investment subject to AB 32 RPS and a 15% planning reserve margin (PRM) constraint.

---

## Prerequisites

### 1. Base PyPSA-USA installation

Follow the [PyPSA-USA installation guide](https://pypsa-usa.readthedocs.io/en/latest/). This codebase is a fork of PyPSA-USA with modifications for California county-level data center demand.

Key software requirements:
- Python ≥ 3.11 (managed via the PyPSA-USA conda/venv environment)
- [Snakemake](https://snakemake.readthedocs.io/) ≥ 7
- [Gurobi](https://www.gurobi.com/) with a valid license (academic license available free)

### 2. Gurobi license

All scenarios require Gurobi. On Stanford's Sherlock HPC:

```bash
export GRB_LICENSE_FILE=/share/software/user/restricted/gurobi/11.0.2/licenses/gurobi.lic
```

For other systems, set `GRB_LICENSE_FILE` to the path of your `gurobi.lic` file.

### 3. PyPSA-USA data

The base PyPSA-USA pipeline requires several large input datasets (breakthrough network, NREL ATB costs, EIA data, weather cutouts). Follow the [data download instructions](https://pypsa-usa.readthedocs.io/en/latest/) in the upstream docs. These files are not included in this repository.

---

## Custom Data: California Data Center Demand

This study adds county-level data center electricity demand on top of PyPSA-USA's baseline EFS load profiles.

The demand files are in `workflow/data/data_centers/`:

| File | Description |
|---|---|
| `ca_county_demand_MWh.csv` | Projected data center annual demand by CA county (GEOID), columns: `county`, `2025`, `2030` |
| `ca_county_demand_MWh_counterfactual.csv` | Counterfactual demand frozen at 2025 levels for all years |

Each county is identified by its 6-character FIPS code prefixed with `p` (e.g., `p06001` = Alameda County). The `2025` and `2030` columns give total annual data center energy consumption in MWh. The pipeline selects the column matching the scenario's `planning_horizons` year.

---

## Repository Structure

```
workflow/
├── config/
│   ├── config.ca_ref2025.yaml                      # Ref 2025 scenario
│   ├── config.ca_ref2030_ab32rps.yaml               # Ref 2030 scenario
│   ├── config.ca_counterfactual2030_ab32rps.yaml    # Counterfactual 2030
│   ├── config.ca_optimized2030_ab32rps.yaml         # Optimized 2030
│   ├── config.ca_counterfactual2030_opt_ab32rps.yaml # Counterfactual 2030 (Opt)
│   ├── config.cluster.yaml                          # Sherlock HPC cluster settings
│   └── policy_constraints/                          # RPS, PRM, CO2, transmission limits
│       ├── SAFE_regional_prm.csv                    # 15% CA planning reserve margin
│       ├── portfolio_standards.csv                  # AB 32 RPS targets
│       └── ...
├── data/
│   └── data_centers/
│       ├── ca_county_demand_MWh.csv
│       └── ca_county_demand_MWh_counterfactual.csv
├── rules/
│   ├── build_electricity.smk                        # Modified: frozen CapEx, base_costs input
│   └── solve_electricity.smk
├── scripts/
│   ├── add_demand.py                                # Modified: county-level DC demand injection
│   ├── add_electricity.py                           # Modified: frozen CapEx for conventional gens
│   ├── add_extra_components.py                      # Modified: frozen CapEx for solar/wind
│   ├── build_demand.py                              # Modified: DC demand shaping
│   ├── opts/
│   │   ├── policy.py                                # Modified: PRM constraint
│   │   └── reserves.py                              # Modified: CA network guard (no AC lines)
│   └── solve_network.py                             # Modified: solver settings
├── run_slurm_ca_ref2025.sh
├── run_slurm_ca_ref2030_ab32rps.sh
├── run_slurm_ca_counterfactual2030_ab32rps.sh
├── run_slurm_ca_optimized2030_ab32rps.sh
└── run_slurm_ca_counterfactual2030_opt_ab32rps.sh
```

---

## Running the Scenarios

All scenarios are run via Snakemake on Stanford's Sherlock HPC cluster. Each scenario has a corresponding SLURM submission script.

### Interactive test (single rule)

```bash
cd workflow
snakemake --configfile config/config.ca_ref2025.yaml --cores 4 -n   # dry run
```

### Batch submission (Sherlock)

Submit each scenario as a Snakemake-managed SLURM job:

```bash
cd workflow
sbatch run_slurm_ca_ref2025.sh                          # Reference 2025
sbatch run_slurm_ca_ref2030_ab32rps.sh                  # Reference 2030
sbatch run_slurm_ca_counterfactual2030_ab32rps.sh        # Counterfactual 2030
sbatch run_slurm_ca_optimized2030_ab32rps.sh             # Optimized 2030
sbatch run_slurm_ca_counterfactual2030_opt_ab32rps.sh    # Counterfactual 2030 (Opt)
```

Each script submits up to 20 parallel Snakemake jobs with 96-hour walltime. Job logs are written to `workflow/logs/`.

### Expected runtime

| Scenario | Approximate runtime |
|---|---|
| Dispatch scenarios (×3) | 6–12 hours each |
| Capacity expansion scenarios (×2) | 12–24 hours each |

---

## Key Code Changes from Base PyPSA-USA

### 1. County-level data center demand (`add_demand.py`, `build_demand.py`)

Data center demand is read from the county-level CSV, spatially matched to network buses, and added as an additional load on top of the baseline EFS demand profile. The demand is shaped to a flat 8,760-hour profile.

### 2. Frozen CapEx for existing generators in 2030 runs (`add_extra_components.py`, `add_electricity.py`)

In optimization scenarios with `planning_horizons: [2030]`, existing generators (built before 2030) should carry 2025 ATB capital costs rather than 2030 ATB values. This prevents the optimizer from over-valuing retirement of plants that were cost-competitive when built.

The fix is in `split_retirement_gens()` in `add_extra_components.py`: existing solar/wind generators receive a capital cost calculated by scaling the locational ATB cost from 2030 back to 2025 levels using the ratio `capex_2025 / capex_2030`, while preserving regional multipliers.

A `base_costs` input (2025 ATB CSV) is passed to `add_extra_components` via `build_electricity.smk` whenever `planning_horizons != 2025`.

### 3. Planning Reserve Margin constraint (`opts/policy.py`, `opts/reserves.py`)

A 15% California PRM is enforced in capacity expansion scenarios via `opts: [RPS-REM-PRM-TCT-3h]`. The PRM constraint is defined in `config/policy_constraints/SAFE_regional_prm.csv`. The reserves module was updated to handle CA-only networks that have no inter-regional AC lines.

---

## Configuration Key Parameters

| Parameter | Dispatch scenarios | Capacity expansion scenarios |
|---|---|---|
| `planning_horizons` | `[2025]` or `[2030]` | `[2030]` |
| `ll` (transmission) | `v1.0` (fixed) | `vopt` (optimizable) |
| `opts` | `REM-TCT-3h` | `RPS-REM-PRM-TCT-3h` |
| `extendable_carriers` | none | solar, onwind, OCGT, CCGT, CCGT-95CCS, batteries |
| `retirement` | `technical` | `economic` |
| `data_centers.demand_file` | `ca_county_demand_MWh.csv` | `ca_county_demand_MWh.csv` or `_counterfactual` |

---

## Citation

If you use this code, please cite the upstream PyPSA-USA model:

> Tehranchi K. et al. (2024). PyPSA-USA: An Open-Source Energy System Optimization Model for the United States. [Zenodo](https://zenodo.org/doi/10.5281/zenodo.10815964).

---

## License

MIT License — see [LICENSE.md](LICENSE.md).
