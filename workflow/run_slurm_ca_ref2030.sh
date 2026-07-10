#!/bin/bash
#SBATCH -p serc
#SBATCH -o logs/snakemake_ca_ref2030.out
#SBATCH -e logs/snakemake_ca_ref2030.err
#SBATCH --time 96:00:00
#SBATCH --mail-type END,FAIL

# cd to the workflow directory (works regardless of where sbatch is called from)
cd "$(dirname "$0")"

# Set SNAKEMAKE to the path of your snakemake executable, or ensure it is on your PATH
SNAKEMAKE="${SNAKEMAKE:-snakemake}"

export GRB_LICENSE_FILE=/share/software/user/restricted/gurobi/11.0.2/licenses/gurobi.lic

$SNAKEMAKE \
  --cluster "sbatch -A {cluster.account} -p {cluster.partition} -o {cluster.output} -e {cluster.error} -c {threads} --mem {resources.mem_mb} --time {resources.walltime}" \
  --cluster-config config/config.cluster.yaml \
  --jobs 20 \
  --latency-wait 60 \
  --configfile config/config.ca_ref2030.yaml \
  --nolock \
  --rerun-incomplete
