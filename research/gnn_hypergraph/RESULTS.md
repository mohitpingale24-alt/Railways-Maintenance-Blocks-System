# GNN + Hypergraph evaluation for RailBlock AI (2026-11-18)

Two proposed enhancements were evaluated against the retained dataset
(deterministic simulation, seed 26092) before being added. One failed its
test and was not added; the other was added as an advisory module.

## 1. GNN risk prediction — tested, no gain on this data, NOT added

A GNN's core computation is aggregating connected assets' features
(message passing). That is directly testable without a neural network:
six graph features were computed per observation — section mean and max
deterioration (excluding self), fraction of section assets above the
repair trigger, section mean previous defects, and the mean/max
deterioration of the nearest assets along the line — and added to the
existing 9 features. Same asset-disjoint chronological split as the
pipeline (train months 0–25 / assets 0–60%, test months 38–41 /
assets 80–100%), logistic regression and gradient boosting, compared on
ROC-AUC, PR-AUC, Brier and Recall@10%.

Test ROC-AUC, base 9 features → base + 6 graph features:

| Horizon | Logistic regression | Gradient boosting |
|---------|--------------------|-------------------|
| 7-day   | 0.6466 → 0.6456    | 0.6671 → 0.6335 (worse) |
| 30-day  | 0.6198 → 0.6182    | 0.6155 → 0.6235 |
| 90-day  | 0.5778 → 0.5759    | 0.5653 → 0.5784 |

PR-AUC, Brier and Recall@10% move the same way: changes within noise,
one clear regression. Why: the simulation generates each asset's
deterioration and failure independently; connected assets share section
and weather context that is already inside the asset's own features.
There is no cross-asset signal in this data for a GNN to learn, and a
GNN would learn these same aggregations at much higher cost (a PyTorch
Geometric dependency and far longer training). Verdict: no GNN model.
If the data-generating process ever gains spatial coupling (for example
a degraded section accelerating its neighbours' wear), re-run
`run_experiment.py` — the gate for revisiting is a graph-feature
ROC-AUC gain beyond noise on the held-out test cohort.

## 2. Hypergraph clustering — added as advisory module

`planner/hypergraph.py` provides `suggested_groups(dataset)`: a
hyperedge joins the tasks that share a section and a day on which one
candidate window can host them together (joint blocks run tasks in
parallel, so the window must fit the longest task plus the 10-minute
setup). STOP_AND_REVIEW tasks are excluded. These groups are
suggestions for the controller only — CP-SAT remains the sole authority
that verifies safety, resources, dependencies and timetable constraints
before any block is proposed.

On the current planning batch (231 eligible tasks, legacy night
period): 14 section-day hyperedges. CP-SAT formed joint blocks on 11
section-days, and all 11 were among the hypergraph suggestions. The 3
remaining suggestions are section-days where that solver run allocated
no joint block (it scheduled 37 of 231 tasks within its time limit).
The groups are deliberately coarse — up to 79 tasks can share a
section-day — so they serve as candidate sets for controller review,
not as final blocks.

## Files

- `run_experiment.py` — the graph-feature experiment (needs the
  simulation database from `npm run data:maharashtra`).
- `results.json` — full metrics for every horizon/model/feature set.
- `hypergraph_prototype.py` — the grouping-vs-CP-SAT comparison.
- `hypergraph_results.json` — its summary counts.
