"""
Average daily California load profile for 2030 (Ref 2030 scenario).
Shows baseline demand + data center demand stacked, with dispatchable capacity line.
"""

import warnings
warnings.filterwarnings("ignore")

import pypsa
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np

NETWORK = (
    "/home/groups/sjdavis/will/pypsa-usa/workflow/results/"
    "CA_Ref2030_AB32RPS/western/networks/"
    "elec_s150_c58_ec_lv1.0_REM-TCT-3h_E.nc"
)
OUT_PDF = "/home/groups/sjdavis/will/pypsa-usa/workflow/results/ca_load_profile_2030.pdf"
OUT_PNG = "/home/groups/sjdavis/will/pypsa-usa/workflow/results/ca_load_profile_2030.png"

# ── Load network ──────────────────────────────────────────────────────────────
n = pypsa.Network(NETWORK)

loads_t = n.loads_t.p_set
ac_cols = [c for c in loads_t.columns if c.endswith(" AC")]
dc_cols = [c for c in loads_t.columns if c.endswith(" DC")]

baseline_mw = loads_t[ac_cols].sum(axis=1)
dc_mw       = loads_t[dc_cols].sum(axis=1)

baseline_mw.index = baseline_mw.index.get_level_values("timestep")
dc_mw.index       = dc_mw.index.get_level_values("timestep")

# ── Average and range by hour of day ─────────────────────────────────────────
hour = baseline_mw.index.hour
avg_baseline = baseline_mw.groupby(hour).mean() / 1e3    # GW
avg_dc       = dc_mw.groupby(hour).mean() / 1e3          # GW

total_mw = baseline_mw + dc_mw
max_load = total_mw.groupby(hour).max() / 1e3

# ── Dispatchable capacity (firm + storage; excludes VRE nameplate) ────────────
vre_carriers = ["solar", "onwind", "offwind", "offwind_floating"]
gens = n.generators[n.generators.p_nom_opt < 1e8]
firm_gw   = gens[~gens.carrier.isin(vre_carriers)].p_nom_opt.sum() / 1e3
storage_gw = n.storage_units.p_nom_opt.sum() / 1e3
dispatch_cap_gw = firm_gw + storage_gw

# ── Plot ──────────────────────────────────────────────────────────────────────
hours = avg_baseline.index.values   # [0, 3, 6, ..., 21]

fig, ax = plt.subplots(figsize=(9, 5))

# Maximum load line
ax.plot(hours, max_load.values,
        color="#4A90D9", linewidth=1.2, linestyle=":", alpha=0.8, label="Maximum Load")

# Stacked average: baseline then data center on top
ax.fill_between(hours, 0, avg_baseline.values,
                color="#4A90D9", alpha=0.80, label="Baseline Demand (avg.)")
ax.fill_between(hours, avg_baseline.values, avg_baseline.values + avg_dc.values,
                color="#E8874A", alpha=0.90, label="Data Center Demand")

# Average total load line
ax.plot(hours, avg_baseline.values + avg_dc.values,
        color="#1a1a2e", linewidth=1.4, zorder=5)

# Dispatchable capacity line
ax.axhline(dispatch_cap_gw, color="#333333", linestyle="--", linewidth=1.8,
           label=f"Dispatchable Capacity ({dispatch_cap_gw:.0f} GW)",
           zorder=6)

total_avg = avg_baseline + avg_dc
peak_hour = total_avg.idxmax()
peak_val  = total_avg.max()
dc_share  = avg_dc.mean() / peak_val * 100

# ── Formatting ────────────────────────────────────────────────────────────────
ax.set_xlim(0, 21)
ax.set_xticks(hours)
ax.set_xticklabels([f"{h:02d}:00" for h in hours], fontsize=9)
ax.set_xlabel("Hour of Day", fontsize=11)
ax.set_ylabel("Load (GW)", fontsize=11)

ax.yaxis.grid(True, linestyle="--", alpha=0.35)
ax.set_axisbelow(True)
ax.spines[["top", "right"]].set_visible(False)
ax.set_ylim(0, dispatch_cap_gw * 1.12)

ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.18),
          ncol=2, fontsize=9, frameon=True, framealpha=0.9)

plt.tight_layout()
plt.subplots_adjust(bottom=0.22)
plt.savefig(OUT_PDF, dpi=150, bbox_inches="tight")
plt.savefig(OUT_PNG, dpi=150, bbox_inches="tight")
print(f"Saved to {OUT_PNG}")

# ── Summary ───────────────────────────────────────────────────────────────────
print(f"\nAvg. peak load:            {peak_val:.1f} GW at {peak_hour:02d}:00")
print(f"Avg. minimum load:         {total_avg.min():.1f} GW at {total_avg.idxmin():02d}:00")
print(f"Avg. data center demand:   {avg_dc.mean():.2f} GW (flat)")
print(f"DC share at avg. peak:     {dc_share:.1f}%")
print(f"Firm + storage capacity:   {dispatch_cap_gw:.1f} GW")
print(f"  Firm generators:         {firm_gw:.1f} GW")
print(f"  Storage:                 {storage_gw:.1f} GW")
