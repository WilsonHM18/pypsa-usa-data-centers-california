#!/bin/bash
#SBATCH -A sjdavis
#SBATCH -p serc
#SBATCH -o /home/groups/sjdavis/will/pypsa-usa/workflow/logs/snakemake_ca_counterfactual2030_opt_ab32rps.out
#SBATCH -e /home/groups/sjdavis/will/pypsa-usa/workflow/logs/snakemake_ca_counterfactual2030_opt_ab32rps.err
#SBATCH --time 96:00:00
#SBATCH --mail-type END,FAIL
#SBATCH --mail-user wmcneil@stanford.edu

cd /home/groups/sjdavis/will/pypsa-usa/workflow
export GRB_LICENSE_FILE=/share/software/user/restricted/gurobi/11.0.2/licenses/gurobi.lic

/home/users/wmcneil/envs/pypsa-usa/bin/snakemake \
  --cluster "sbatch -A {cluster.account} -p {cluster.partition} -o {cluster.output} -e {cluster.error} -c {threads} --mem {resources.mem_mb} --time {resources.walltime}" \
  --cluster-config config/config.cluster.yaml \
  --jobs 20 \
  --latency-wait 60 \
  --configfile config/config.ca_counterfactual2030_opt_ab32rps.yaml \
  --nolock \
  --rerun-incomplete
