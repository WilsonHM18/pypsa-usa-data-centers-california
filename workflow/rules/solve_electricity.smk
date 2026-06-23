# Rules to Optimize/Solve Network


def pop_layout_input(wildcards):
    if wildcards["sector"] != "E":
        return RESOURCES + "{interconnect}/pop_layout_elec_s{simpl}_c{clusters}.csv"
    else:
        return []


def ev_policy_input(wildcards):
    if wildcards["sector"] != "E":
        return "config/policy_constraints/ev_policy.csv"
    else:
        return []


rule solve_network_sb886:
    """
    SB 886 two-stage solve: stage 1 without DC loads, stage 2 with DC loads
    + hourly 50% zero-carbon matching constraint, using stage-1 capacities as
    lower bounds.  Outputs the stage-2 solved network plus a cost-allocation
    CSV (incremental capex including shared tx upgrades per §8542(c)(5)(B)).
    """
    params:
        solving=config_provider("solving"),
        foresight=config_provider("foresight"),
        planning_horizons=config["scenario"]["planning_horizons"],
        transmission_network=config_provider("model_topology", "transmission_network"),
    input:
        network=RESOURCES
        + "{interconnect}/elec_s{simpl}_c{clusters}_ec_l{ll}_{opts}_{sector}.nc",
        flowgates="repo_data/ReEDS_Constraints/transmission/transmission_capacity_init_AC_ba_NARIS2024.csv",
        safer_reeds="config/policy_constraints/reeds/prm_annual.csv",
        rps_reeds="config/policy_constraints/reeds/rps_fraction.csv",
        ces_reeds="config/policy_constraints/reeds/ces_fraction.csv",
    output:
        network=RESULTS
        + "{interconnect}/networks/elec_s{simpl}_c{clusters}_ec_l{ll}_{opts}_{sector}_sb886.nc",
        allocation=RESULTS
        + "{interconnect}/sb886/cost_allocation_s{simpl}_c{clusters}_l{ll}_{opts}_{sector}.csv",
        config=RESULTS
        + "{interconnect}/configs/config.elec_s{simpl}_c{clusters}_l{ll}_{opts}_{sector}_sb886.yaml",
    log:
        solver_stage1=normpath(
            LOGS
            + "solve_network_sb886/{interconnect}/elec_s{simpl}_c{clusters}_ec_l{ll}_{opts}_{sector}_stage1_solver.log"
        ),
        solver_stage2=normpath(
            LOGS
            + "solve_network_sb886/{interconnect}/elec_s{simpl}_c{clusters}_ec_l{ll}_{opts}_{sector}_stage2_solver.log"
        ),
        python=LOGS
        + "solve_network_sb886/{interconnect}/elec_s{simpl}_c{clusters}_ec_l{ll}_{opts}_{sector}_python.log",
    benchmark:
        (
            BENCHMARKS
            + "solve_network_sb886/{interconnect}/elec_s{simpl}_c{clusters}_ec_l{ll}_{opts}_{sector}"
        )
    threads: solver_threads
    resources:
        walltime=config_provider("walltime", "solve_network_sb886", default="40:00:00"),
        mem_mb=lambda wildcards, input, attempt: (input.size // 100000) * attempt * 300,
    conda:
        "../envs/environment.yaml"
    script:
        "../scripts/solve_network_sb886.py"


rule solve_network_ab2383:
    """
    AB 2383 two-stage solve: stage 1 without DC loads, stage 2 with DC loads
    using stage-1 capacities as lower bounds.  Outputs the stage-2 solved
    network plus a cost-allocation CSV (incremental capex / DC energy).
    """
    params:
        solving=config_provider("solving"),
        foresight=config_provider("foresight"),
        planning_horizons=config["scenario"]["planning_horizons"],
        transmission_network=config_provider("model_topology", "transmission_network"),
    input:
        network=RESOURCES
        + "{interconnect}/elec_s{simpl}_c{clusters}_ec_l{ll}_{opts}_{sector}.nc",
        flowgates="repo_data/ReEDS_Constraints/transmission/transmission_capacity_init_AC_ba_NARIS2024.csv",
        safer_reeds="config/policy_constraints/reeds/prm_annual.csv",
        rps_reeds="config/policy_constraints/reeds/rps_fraction.csv",
        ces_reeds="config/policy_constraints/reeds/ces_fraction.csv",
    output:
        network=RESULTS
        + "{interconnect}/networks/elec_s{simpl}_c{clusters}_ec_l{ll}_{opts}_{sector}_ab2383.nc",
        allocation=RESULTS
        + "{interconnect}/ab2383/cost_allocation_s{simpl}_c{clusters}_l{ll}_{opts}_{sector}.csv",
        config=RESULTS
        + "{interconnect}/configs/config.elec_s{simpl}_c{clusters}_l{ll}_{opts}_{sector}_ab2383.yaml",
    log:
        solver_stage1=normpath(
            LOGS
            + "solve_network_ab2383/{interconnect}/elec_s{simpl}_c{clusters}_ec_l{ll}_{opts}_{sector}_stage1_solver.log"
        ),
        solver_stage2=normpath(
            LOGS
            + "solve_network_ab2383/{interconnect}/elec_s{simpl}_c{clusters}_ec_l{ll}_{opts}_{sector}_stage2_solver.log"
        ),
        python=LOGS
        + "solve_network_ab2383/{interconnect}/elec_s{simpl}_c{clusters}_ec_l{ll}_{opts}_{sector}_python.log",
    benchmark:
        (
            BENCHMARKS
            + "solve_network_ab2383/{interconnect}/elec_s{simpl}_c{clusters}_ec_l{ll}_{opts}_{sector}"
        )
    threads: solver_threads
    resources:
        walltime=config_provider("walltime", "solve_network_ab2383", default="40:00:00"),
        mem_mb=lambda wildcards, input, attempt: (input.size // 100000) * attempt * 300,
    conda:
        "../envs/environment.yaml"
    script:
        "../scripts/solve_network_ab2383.py"


rule solve_network:
    params:
        solving=config_provider("solving"),
        foresight=config_provider("foresight"),
        planning_horizons=config["scenario"]["planning_horizons"],
        co2_sequestration_potential=config["sector"].get(
            "co2_sequestration_potential", 200
        ),
        transmission_network=config_provider("model_topology", "transmission_network"),
    input:
        network=RESOURCES
        + "{interconnect}/elec_s{simpl}_c{clusters}_ec_l{ll}_{opts}_{sector}.nc",
        flowgates="repo_data/ReEDS_Constraints/transmission/transmission_capacity_init_AC_ba_NARIS2024.csv",
        safer_reeds="config/policy_constraints/reeds/prm_annual.csv",
        rps_reeds="config/policy_constraints/reeds/rps_fraction.csv",
        ces_reeds="config/policy_constraints/reeds/ces_fraction.csv",
        pop_layout=pop_layout_input,
        ev_policy=ev_policy_input,
    output:
        network=RESULTS
        + "{interconnect}/networks/elec_s{simpl}_c{clusters}_ec_l{ll}_{opts}_{sector}.nc",
        config=RESULTS
        + "{interconnect}/configs/config.elec_s{simpl}_c{clusters}_l{ll}_{opts}_{sector}.yaml",
    log:
        solver=normpath(
            LOGS
            + "solve_network/{interconnect}/elec_s{simpl}_c{clusters}_ec_l{ll}_{opts}_{sector}_solver.log"
        ),
        python=LOGS
        + "solve_network/{interconnect}/elec_s{simpl}_c{clusters}_ec_l{ll}_{opts}_{sector}_python.log",
    benchmark:
        (
            BENCHMARKS
            + "solve_network/{interconnect}/elec_s{simpl}_c{clusters}_ec_l{ll}_{opts}_{sector}"
        )
    threads: solver_threads
    resources:
        walltime=config_provider("walltime", "solve_network"),
        mem_mb=lambda wildcards, input, attempt: (input.size // 100000) * attempt * 150,
    conda:
        "../envs/environment.yaml"
    script:
        "../scripts/solve_network.py"
