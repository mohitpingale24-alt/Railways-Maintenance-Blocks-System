# Maharashtra controller upgrade — measured results

The Controller now opens a four-zone planning workspace with department tabs, date/corridor/section filters, timetable provenance, candidate work windows, ranked maintenance requests and CP-SAT assignment results. Existing navigation and components remain available. The simulation backend reads a separate generated lifecycle database. This is academic planning software, not railway dispatch authorization.

## Run locally

From the project directory:

```sh
npm install
python3 -m venv .venv
.venv/bin/pip install -r planner/requirements.txt
npm run data:maharashtra
npm run db:setup
npm run dev
```

The data and models have already been generated in this workspace. After Python source changes, stop the existing launcher with Ctrl+C and run `npm run dev` again. Open http://localhost:3000/control-centre; CONTROL-01 / RailOps@123. Simulation planner has the four zone cards, three department queues, selected-date timetable references and candidate free windows. Select a zone → department → corridor → date → section; adjust weights, crew pools or an eligible request's duration/crew; then run CP-SAT. A plan retains its source/model snapshot; changed inputs clear the displayed draft and change its fingerprint.

Regeneration replaces only `.railblock` simulation artifacts. It does not delete `.wrangler` operational jobs, source requests, credentials or sessions. It invalidates approval of plans generated against previous model/data fingerprints. Do not regenerate during a planning/review session. The 500,000 requests include completed history and 12,000 current pending requests, rather than 500,000 simultaneous unfinished jobs for the same assets.

## Actual data counts

| Record | Count |
|---|---:|
| requests | 500,000 |
| assets | 12,000 |
| inspections | 576,000 |
| completed_maintenance | 74,450 |
| failure_events | 15,302 |
| pending | 12,000 |

| Department | Requests | Inspection history |
|---|---:|---:|
| TRK | 166,667 | 192,000 |
| SIG | 166,667 | 192,000 |
| TRC | 166,666 | 192,000 |

Seed: 26092. The generator simulates 48 monthly inspection snapshots and daily deterioration/intervention/failure events for 12,000 persistent assets. Damage responds to age, exposure, seasonal stress, defect history, latent asset frailty, stochastic shocks and repairs. Labels record actual subsequent simulated failure events within 7/30/90 days. Duration targets come from completed simulated interventions. Request rows join those histories, assets and observations; they are not independently randomized records. Assets, request IDs, event histories, positions and operating scenarios are marked SYNTHETIC.

Storage uses indexed SQLite with normalized observations, completions, assets and requests, rather than shipping half a million JSON records into the browser. Predictions are produced in 10,000-row batches. The queue uses database filtering and pagination. Each optimization considers the top 300 pending requests in the selected scope; additional pending work remains outside that bounded batch. This is not a claim to solve all 500,000 requests simultaneously.

## Geography and public timetable coverage

There are four project-defined zones, 12 corridors, 19 unique coarse planning sections and 24 station references. Shared physical endpoint pairs have exactly one owning zone. Pune–Daund is reused by Pune–Solapur, and Wardha–Nagpur by the two Vidarbha corridors; neither edge is duplicated. Sections are coarse endpoint-to-endpoint planning abstractions, not a surveyed track topology.

Six station coordinates were verified from secondary station references: NGP, PUNE, MMR, BSL, NK, AK. Each carries its source URL. Other coordinates remain explicitly unverified rounded map references. All asset positions are synthetic straight-line endpoint interpolation; section lengths are synthetic distance proxies, never official railway chainages.

53 selected train/direction timetable references exist (reconciled with `timetable.catalogue()` on 17 November 2026): the original 42 Pune records and eleven added endpoint references. The Western Railway 92213 row cites the official PTT78 May 2026 publication, Churchgate 06:00 → Virar 07:22. Other additions are secondary references, including 12859 Gitanjali, 12169 Solapur Intercity, and 17617 Tapovan. Every one of the 53 records now carries listed operating days from a checked public listing (the `CHECKED`/`CHECKED_DAILY` promotions in `timetable.py`), so all 53 are projected into movements on their listed days; an earlier draft of this document said only two records listed service days and 48 were unverified — that described the superseded 50-record catalogue. A listed day still does not verify that the timetable remains current. The UI separates published references from movements used for the chosen date and reports missing coverage instead of inventing trains.

| Corridor | Selected references | Coverage |
|---|---:|---|
| Mumbai-Kalyan | 1 | INCOMPLETE public reference |
| Churchgate-Virar | 1 | INCOMPLETE public reference |
| Panvel-Ratnagiri | 1 | INCOMPLETE public reference |
| Pune-Lonavala | 16 | INCOMPLETE public reference |
| Pune-Daund | 16 | INCOMPLETE public reference |
| Pune-Solapur | 1 | INCOMPLETE public reference |
| Pune-Miraj | 10 | INCOMPLETE public reference |
| Nashik-Manmad-Bhusawal | 2 | INCOMPLETE public reference |
| Manmad-Sambhajinagar-Nanded | 1 | INCOMPLETE public reference |
| Bhusawal-Akola-Nagpur | 2 | INCOMPLETE public reference |
| Nagpur-Wardha-Chandrapur | 1 | INCOMPLETE public reference |
| Nagpur-Gondia | 1 | INCOMPLETE public reference |

Only the actual published endpoint interval for a record with listed service days is projected onto its traversed coarse sections, conservatively reserving both lines. Passing times are not fabricated. Every corridor now has at least one retrieved reference, and shared sections can also inherit protected movements from another corridor. Freight, specials, non-stopping movements, cancellations, live delays, current service dates and possession restrictions are incomplete throughout. Candidate gaps assume listed sources only and are always marked conditional; they are not proof of a free track or an authorized maintenance block. The separate synthetic operating scenario uses SYN-prefixed service IDs and is never merged into public references.

Source URLs and publication/retrieval versions are stored in `planner/maharashtra/timetable.py`; coordinate and route provenance is stored in `network.py`. The Central Railway 2026 Pune division map was researched for the Pune route identities. The project does not claim official verification for every coarse section or coordinate.

## Genuine trained models and held-out performance

Training uses 24,000 sampled observations from the training asset cohort and months 0–25. Model selection uses 9,600 observations from distinct assets and months 30–33. Final testing uses 9,600 observations from another distinct asset cohort and months 38–41. The gaps between observation periods exceed the 90-day outcome horizon. Only training data fit estimators; validation chooses candidates; test data are used for final reporting.

Compared classifiers: standardized Logistic Regression, Decision Tree, Random Forest and two Gradient Boosting configurations. Duration candidates: historical mean baseline, Random Forest and Gradient Boosting. Selection minimizes validation Brier score or duration MAE. All selected estimators are saved with joblib; the application verifies their SHA-256 against database metadata. Changing finite observation features through the authenticated `infer` action recomputes risk, duration, the six contributions and their total from these saved models. No score lookup or hardcoded prediction is used. Importance is normalized positive permutation importance of the selected 30-day classifier on held-out observations, not a causal estimate.

| Horizon | Selected model | ROC-AUC | Brier | Precision @0.5 | Recall @0.5 | F1 @0.5 |
|---|---|---:|---:|---:|---:|---:|
| 7 days | Logistic regression | 0.5996 | 0.00498 | 0.000 | 0.000 | 0.000 |
| 30 days | Logistic regression | 0.6120 | 0.02745 | 0.000 | 0.000 | 0.000 |
| 90 days | Random forest depth 8 | 0.5772 | 0.07641 | 0.000 | 0.000 | 0.000 |

Duration model: Gradient boosting; holdout MAE **10.43 min**, RMSE **13.98 min**, R² **0.631**, 1,378 completed held-out interventions. A **21.29-minute** 90th-percentile validation residual buffer is added to each duration estimate, followed by ten minutes of setup/clearance in the scheduler. This is an uncertainty allowance, not a guaranteed duration bound.

These models are weak classifiers on rare synthetic outcomes. At the reported 0.5 threshold they predict no positive failures; zero precision/recall/F1 is retained, not hidden behind accuracy. Probability bins, prevalence, all candidate validation scores and exact metrics are saved in `.railblock/evaluation.json`. No independent severity model is trained: severity is observed simulated inspection evidence. Performance on generated history does not establish real Indian Railway predictive accuracy. No observed freight model is activated; unknown freight is shown as unknown, not a zero-traffic claim.

## Priority, safety and scheduling

Default weights are condition 15 + safety 25 + urgency 15 + predicted 30-day risk 25 + overdue 10 + operational exposure 10 = 100 configurable project points. Each contribution is calculated from its normalized input and rounded to one decimal; the displayed total is the rounded sum. The request detail dialog lists every raw input, normalized value, weight, contribution and all 7/30/90-day saved-model risks, plus duration buffer and simulated severity. These weights are not official railway policy.

Class-5 simulated inspection evidence is STOP_AND_REVIEW independently of score and is withheld from routine optimization. Urgent disruptions change emergency scheduling treatment and deadlines without falsifying the displayed score breakdown. Mixed power-isolation work requires confirmed separation in the Maharashtra scenario; generated records do not presume that confirmation.

CP-SAT uses listed movements plus five-minute headway, bounded synthetic 00:00–06:00 candidate periods, full duration plus setup, release/deadline constraints, resource pools, machines, shared station-yard exclusions, dependencies and the existing pairwise bundling rules. Candidate periods and their gaps are recomputed from selected movement inputs; they are not officially authorized possessions. The existing Gantt shows a full selected day, published/reference train IDs on hover, conditional gaps and optimizer results. Freight disruptions subtract from plotted gaps.

The Controller includes six configurable priority weights that must sum to 100, department crew-pool capacities, and per-request crew/duration overrides for requests in the active 300-request batch. A changed weight re-scores that batch from its original observation values and saved-model 30-day predictions; changed inputs are fingerprinted and CP-SAT runs again. Other pending requests remain paginated and are considered through subsequent 300-request drafts. Batch drafts are independent alternatives; they are not combined or globally optimal. The existing bundling heuristic remains heuristic. Baseline comparisons include only the same retained work and may report zero savings. Human accept/reject, manual edits, saved history, weekly/monthly planning, feedback and disruption reoptimization remain in place. Nothing dispatches crews or releases real railway work from this simulation.

## Final reproducible four-zone scheduling checks

The audit uses 28 actual model-scored requests per zone (24 routine + 4 stop-and-review), for bounded, fast solver validation. It also adds a separate synthetic full-horizon closure; because tasks are optional, the correct outcome is zero scheduled and all deferred, not a CP-SAT “infeasible” status.

| Zone | Feasible run status | Scheduled | Blocks | Shared blocks | Closed-window run | Zero-crew run |
|---|---|---:|---:|---:|---|---:|
| Z1 Mumbai & Konkan | OPTIMAL | 18 | 17 | 1 | 0 scheduled / 28 deferred | 0 |
| Z2 Pune & Western Maharashtra | FEASIBLE | 10 | 10 | 0 | 0 scheduled / 28 deferred | 0 |
| Z3 North Maharashtra & Marathwada | FEASIBLE | 11 | 10 | 1 | 0 scheduled / 28 deferred | 0 |
| Z4 Vidarbha | FEASIBLE | 13 | 12 | 1 | 0 scheduled / 28 deferred | 0 |

Each feasible plan passed the schedule validator with zero violations. The same saved model and zone/department requests were used. A separate one-slot competition check confirms that changing the six weights can change the chosen request; additional audit inputs change crew, requested duration, corridor, capacity and resulting allocation fingerprint. Full inputs and results are saved in `.railblock/controller-validation.json`; the priority-competition check is `scripts/validate-priority-recompute.py`, which records its result inside that same file under `changes.priority_competition` — there is no separate `.railblock/priority-recompute.json` artifact (an earlier draft of this document referenced one in error). Note the saved validation artifact's embedded timetable-coverage snapshot still describes the superseded 50-reference catalogue; re-run `python3 scripts/validate-controller.py` after restarting the planner to refresh it against the current 53-reference catalogue.

## Validation and remaining limits

Final verification: see `.railblock/controller-validation.json` for the all-zone/dept/model/solver audit, including one feasible, one zero-allocation closure, and a zero-crew check in each zone. Model metrics and score/data provenance checks are in `.railblock/model-audit.json`. Browser verification is pending the local Python engine restart so it serves the latest timetable and zone-summary code.

Commands: `npm run test:planner`, `npm run test:maharashtra`, `node scripts/test-shifts.mjs`, `node scripts/test-request-import.mjs`, `npx tsc --noEmit`, `npm run build`.

Tests cover full-store counts, referential integrity, all 500,000 score sums and risk horizon ordering, source/synthetic separation, weighted request queues constrained to the active batch, all-zone/department counts, filters, work-type search, actual saved-model inference, changed inputs, resource fingerprints, all four zones, blocked windows, zero resource capacity, competing priorities, critical-review exclusions, protected train conflicts and monthly scheduling. Existing planner, operational solver and freight-model tests are also retained.

Chrome shows the new four-zone and three-department workspace. The already-running local Python process predates the latest source changes: it returns stale references and does not populate the new live request summaries. Do not treat that browser state as final validation. After restarting `npm run dev`, verify the selected zone tabs, department counts, source warnings, first-300 queue, saved predictions, changed-input recomputation and conditional Gantt allocations in Chrome.

Still incomplete: full official timetable coverage, current service-day/effective-date verification, observed freight history, surveyed track coordinates/topology, real asset/inspection/failure labels, operational model validation and live railway authorization. Detailed source provenance and all synthetic labels remain visible. Restart the local launcher, refresh the Controller, generate a fresh plan, and keep recommendations conditional.

### Selected timetable references updated 17 November 2026

The catalogue now contains 53 selected endpoint references, each with listed operating days; all 12 project corridors and all modeled sections have at least one such reference. Pune route listings were checked against direction-specific public tables; selected references for other regions were checked against train-specific listings. Some revised endpoint times differ from earlier transcriptions. A train crossing multiple coarse sections conservatively reserves its full published endpoint interval on each section; actual intermediate passing times are not inferred. The catalogue is still a small sample, not a complete or live timetable: missing services, freight, cancellations and current restrictions remain unknown. Existing plans are snapshots and need a fresh solve after restarting the Python planner.

### Audit fixes applied 17 November 2026 (zero-allocation and performance audit)

- **Pune–Lonavala zero allocations are measured infeasibility, not a solver bug.** Tracing timetable → movements → candidate windows → CP-SAT with the project's own code: 16 listed daily services produce 14 movements/day on MH-SEC-04 (Pune–Lonavala). After the full published endpoint intervals and 5-minute headways are removed from the synthetic 00:00–06:00 period, the remaining gaps are 20, 5 and 88 minutes — every day, because all 16 services are daily. The longest candidate window is therefore **88 minutes**, while the smallest possible block is predicted duration + 21.3-minute buffer + 10-minute setup; even a 60-minute predicted duration needs 92 minutes, and observed predictions are 150–200 minutes. Feasible task-window pairs: 0. The same arithmetic leaves Pune–Daund (MH-SEC-05) a 30-minute longest gap and Pune–Miraj sections a 0-minute gap, so zero allocations there are also expected under the current conservative full-journey occupation model. No slots were invented and no duration or safety constraint was relaxed to change this.
- **Diagnostics are now exposed instead of a bare OPTIMAL/0 result.** `engine.window_report()` measures movements considered, candidate gaps, longest window and total candidate minutes per section; `solve()` returns `window_analysis` (per section: groups, feasible groups, candidate intervals, minimum required block minutes); plans with no allocations carry explicit `warnings`; per-request deferral reasons for the empty-window case now state the measured longest window, the required block minutes and the zero feasible-pair count. `store.dataset()` likewise returns `window_diagnostics` including `feasible_task_window_pairs` per section.
- **Availability wording corrected.** Plan metrics now include `availability_basis` and `availability_note`: when no blocks are scheduled, the note states the figure only means no track time was consumed and does **not** mean the requested work was done.
- **Freight stays explicitly inactive.** No observed freight dataset exists anywhere in this workspace (no `data/ml/` directory, no uploaded history). `store.dataset()` now returns a `freight_status` object saying so, with the activation requirement (≥42 complete observed days per section plus the baseline gates). Simulated freight in scenarios is labelled a simulated assumption, never an observed record.
- **Zone-summary aggregation is cached** against the simulation database file stamp (mtime + size) instead of re-scanning all 500,000 requests on every call; a database rebuild changes the stamp and refreshes the counts.
- **Legacy scenario cold-start refitting removed.** The legacy scenario is a deterministic function of its version and seed, and its model fits only produce metrics/risks stored as plain data in the result, so the built scenario is now cached at `.railblock/legacy-scenario-cache.pkl` and reused on later starts (any cache problem falls back to a full rebuild).
- **Concurrency contract made explicit.** The planner service runs one planning/model job at a time; a concurrent request is rejected with HTTP 409 and `code: PLANNING_BUSY` (`behaviour: rejected_not_queued`). Requests are never queued and multi-user concurrent planning is not claimed.
- **Dependencies declared.** `planner/requirements.txt` now lists `numpy` and `joblib` explicitly instead of relying on scikit-learn's transitive dependencies.
- **React concurrent-renderer warning investigated.** It appears only in development-server (HMR) logs; the app has a single root layout and no duplicate context provider or `createRoot` in its own code, so it is a dev-renderer artefact of the vinext/Vite toolchain, not an application bug. It does not appear in production builds.
- **Failure-risk model quality re-audited with a leakage-safe comparison** (project `simulate()` + the exact asset-disjoint, chronological, 120-day-purged splits; candidates selected on validation Brier only, test cohort untouched until reporting; replication reproduced the saved test AUCs 0.5996 / 0.6120 / 0.5772 exactly). Findings: (1) **No target leakage** — labels count failures strictly after the inspection day; `previous_defects` includes the inspection day itself, which is known at prediction time. (2) **Two feature pairs are duplicates of one signal each**: `overdue_days` is exactly `max(0, last_maintenance_days - 120)` (verified deviation 0.0, correlation 0.95), and `inspection_measurement` is a rescaled copy of the same noisy damage reading as `deterioration` (correlation 0.986). Per-horizon permutation importance is dominated by one duplicated pair at a time (30-day: last-maintenance 0.57 + overdue 0.40). (3) **No candidate genuinely beats the saved selection**: validation Briers for Logistic Regression / Decision Tree / Random Forest / Gradient Boosting differ by at most 0.0002 per horizon; Gradient Boosting (leaves 7) shows slightly higher test ROC-AUC (0.621 / 0.624 at 7/30 days) but not lower validation Brier, so switching would be test-set selection. Class-weighted Logistic Regression keeps AUC but destroys probability calibration (validation Brier 0.24). Test PR-AUC is 0.008 / 0.040 / 0.112 against prevalences 0.005 / 0.028 / 0.084 — the models rank weakly (top-10% of 30-day scores capture 15% of failures) and are not threshold classifiers; at 0.5/0.2/0.1 thresholds they flag essentially nothing at 7/30 days. (4) **Validation-fitted calibration does not demonstrably help**: Platt and isotonic calibration fitted on validation change untouched-test Brier by at most 0.0003 (7-day: no change; 30-day Platt ECE 0.0017 → 0.0008; 90-day isotonic Brier 0.07641 → 0.07614 with ECE unchanged), so calibration was **not** adopted and the uncalibrated-risk warnings stand. The saved model bundle is preserved unchanged. The ~0.6 AUC is a signal ceiling of the synthetic process (latent damage observed through noise, stochastic shocks, repair resets), not a training defect.
- Restart `npm run dev` before browser verification: the running planner predates these source changes.

## XGBoost / Random Forest improvement pass and ML Model Comparison tab (17 November 2026)

The training pipeline now evaluates Logistic Regression, Decision Tree, Random Forest (existing + tuned), Gradient Boosting (leaves 7/15) and XGBoost classifiers/regressors on the same asset-disjoint, chronological, 120-day-purged splits, on both the original 9 features and a leakage-safe engineered set (deterioration/measurement 1-month deltas, defect deltas over 1 and 3 months, 3-month deterioration mean, last-maintenance delta — all computed only from the same asset's current and earlier inspections). Selection stays on validation (Brier for risk, MAE for duration), but a challenger now replaces the incumbent configuration only past a pre-set meaningful margin (0.5% relative Brier; 2% relative MAE); the untouched test cohort is reported once.

Outcome of the verified run (identical pipeline code and seed, executed in the sandbox; the Mac bundle regenerates with `npm run data:maharashtra` after installing the updated `planner/requirements.txt`, which now includes xgboost):

- **Risk models unchanged.** No challenger cleared the margin: XGBoost classifiers never won a horizon on validation Brier (best 0.02415 at 30 days vs Logistic Regression 0.02407); tuned Random Forests were worse than the existing depth-8 configuration; class-weighted variants destroyed calibration. Engineered features improved 30-day *ranking* (Gradient Boosting AUC 0.612 → 0.634, PR-AUC 0.040 → 0.047) but not validation Brier (0.02406 vs 0.02407), and probabilities — not just ranks — feed the Criticality Index, so the feature migration was not adopted for risk. Deployed: Logistic Regression (7/30 days), Random Forest depth 8 (90 days), test ROC-AUC 0.5996 / 0.6120 / 0.5772, uncalibrated (validation-fitted Platt/isotonic recalibration was evaluated per horizon and did not meet the adoption rule).
- **Duration model replaced.** XGBoost regressor (depth 4, lr 0.05, 400 trees) on engineered features: validation MAE 9.01 vs incumbent Gradient Boosting (base features) 10.16 (−11.3%); untouched test MAE 10.44 → 9.34 minutes, RMSE 13.98 → 12.66, R² 0.631 → 0.689. The validation p90 buffer shrinks 21.3 → 18.0 minutes as a direct consequence. `store.infer` rebuilds the engineered inputs from the asset's stored inspection history (observation ids are the 0-based flat index), and reproduces stored request risks/durations exactly (verified 7/7 sampled requests). Predicted durations continue to feed CP-SAT unchanged; old plans invalidate through the existing dataset fingerprint because stored durations change on regeneration.
- **Comparison tab.** The pipeline writes `.railblock/model-comparison.json` at build time (all candidates' validation + test metrics, ROC/PR/calibration curves, confusion matrices at 0.05–0.5, per-department and per-zone breakdowns, permutation importances, train/inference timings, selection decisions). `planner/server.py` serves it at POST `/model-comparison` via `store.model_comparison()` (which flags any hash mismatch with the deployed bundle), `app/api/model-comparison/route.ts` gates it to signed-in controllers, and the Controller gains exactly one new tab, **ML Model Comparison**, rendering only artifact data. Until the Mac retrains, the shipped artifact describes the sandbox-trained bundle and the tab shows the hash-mismatch banner — that is the honest state, not an error.

## Repair pass — delay scenarios, analyze 404, comparison-tab typing (17 November 2026, later)

Root causes of the four reported failures, traced end to end:

- **test_delay scenarios 3/4 (engine): stale fixtures, not a solver bug.** The Pune–Lonavala tasks the original fixtures picked were `STOP_AND_REVIEW` requests, which CP-SAT withholds from scheduling by design — the failing run's own logs show `eligible_tasks: 0` for the trimmed solves, so no baseline could ever schedule them. The fixtures now exclude STOP_AND_REVIEW tasks (same rule `section_tasks` already used). With the current fixtures, the scenario-3 block (78-minute work + 10-minute setup = 88) has exactly one feasible start per day in the measured (272, 360) gap and an empty domain after the +30 delay on all 7 days, deferring with the measured candidate-gap reason; scenario 4's two competitors both reduce to the single day-0 start (272) after days 1–6 are closed, and the section cumulative capacity of 1 admits exactly one — the higher-priority request. All 11 delay-layer tests pass on the current timetable; no engine, delay or timetable code changed.
- **POST /delay/analyze 404: outdated running backend.** The route chain is complete in current code — Controller action `delay_analyze` in `app/api/planner/route.ts` → engine `POST /delay/analyze` in `planner/server.py` → `delay.analyze`. The 404 body (`{'error': 'Not found'}`) came from a planner process started before the delay wiring was restored to server.py. Verified against the current server.py: `POST /delay/analyze` returns 200 with the full gap/fingerprint analysis, and unknown paths return the same 404 body the failing run saw. Fix: restart `npm run dev` so the planner reloads server.py; no code change was needed or made.
- **model-comparison.tsx TS18046: fixed properly.** The tab now declares full TypeScript interfaces for the comparison artifact and validates the fetched JSON structurally at the boundary (`parseComparisonResponse`) before narrowing — the earlier `as any` patch is gone, and the file contains no `any`, casts of convenience or suppression comments. It type-checks clean under strict mode and renders the same UI; malformed or missing artifacts land in the existing graceful error/empty states.

## Score improvement pass — full training data, polynomial terms, AUC tie-break (17 November 2026, evening)

The deployed risk scores genuinely improved. Root cause of the earlier plateau was not the models — it was data starvation: the training pipeline subsampled the asset-disjoint training cohort to 24,000 of 187,200 available inspection pairs. An oracle check (a gradient-boosting model given the simulation's TRUE latent damage and frailty) confirmed the probability ceiling: its validation Brier (0.00542 / 0.02410 / 0.06536) is no better than the deployed models', so Brier was already at the information limit of the synthetic process — but ranking (ROC-AUC) still had headroom, and more training data plus the right functional form captured it.

Changes to `planner/maharashtra/pipeline.py`:

- **No more subsampling caps** — risk and duration training use the full cohorts (training rows 24,000 → 187,200).
- **New 'poly' feature set** — deterioration squared/cubed (the simulated hazard is cubic in deterioration), a repair-trigger indicator (deterioration > 0.63 makes an inspection-day repair likely, which itself suppresses subsequent failures), and deterioration × load / × age interactions, all derived from the current observation only. `store.infer` expands the same features at prediction time (`pipeline.poly_row`).
- **Selection rule, documented in the artifact** — lowest validation Brier still wins outright at ≥ 0.5% relative improvement; within that statistical-tie band, the Brier-tied candidate with the highest validation ROC-AUC is selected, because the risk models are used as rankers for the Criticality Index. Test cohort remains untouched during selection.

Verified outcome (identical code + seed, full rebuild in the sandbox; untouched test cohort):

| Target | Before | After | Test Brier before → after |
|---|---|---|---|
| 7-day risk | Logistic regression (base), AUC 0.5996 | Logistic regression (engineered), AUC 0.6607 | 0.00498 → 0.00497 |
| 30-day risk | Logistic regression (base), AUC 0.6120 | Gradient boosting leaves 7 (engineered), AUC 0.6428 | 0.02745 → 0.02733 |
| 90-day risk | Random forest depth 8 (base), AUC 0.5772 | Logistic regression (poly), AUC 0.6069 | 0.07641 → 0.07597 |
| Duration | XGBoost (engineered), MAE 9.34, R² 0.689 | unchanged | buffer 17.96 min |

Every horizon improved on both ranking and probability quality; inference reproduces stored predictions exactly (7/7 sampled requests, all horizons), monotonicity P(7) ≤ P(30) ≤ P(90) holds for all 500,000 requests, and the comparison artifact's hash matches the newly trained bundle. Retraining on the Mac (`npm run data:maharashtra`) now takes substantially longer than the capped version (the full-cohort candidate sweep ran ~15 minutes on a throttled 1.5-CPU sandbox; expect several minutes on the Mac) — that is the honest cost of training on all the data. Until the Mac retrains, the shipped artifact describes the sandbox-trained bundle and the tab shows the hash-mismatch banner.

## Lifecycle v2 — a more predictable simulated world (18 November 2026)

The v1 world's ceiling was the world itself, not the models. Its daily hazard (0.006·damage³, capped at 0.08) gave even the worst asset only ~4% probability of failing within 7 days, so no honest model could ever output a probability near 0.5 — precision, recall and F1 at the standard 0.5 threshold were identically 0 for every candidate at every horizon, and ranking by the TRUE latent damage capped at AUC 0.71 (measured by oracle experiment). The system therefore ranked well but could never classify. `mh-lifecycle-v2` redesigns the simulated world so that observed condition genuinely determines near-term failure. Only `simulate()` changed — models, feature sets, splits, selection rule and the Criticality Index are untouched. Same seed (26092), same 12,000 assets, 500,000 requests, 576,000 inspections, same 9 features, same label definitions.

Changes inside `simulate()`:

- **Steep hazard.** Daily failure probability is now `clip(0.00004 + 0.9·damage^10 + 0.00012·load + 0.00001·age_years, 0, 0.5)` (was `damage³·0.006`, cap 0.08). An asset near the damage ceiling is now genuinely likely to fail within days; a mid-condition asset is not.
- **Faster deterioration.** Base damage growth 0.0011 → 0.0016 per day, so more assets reach the high-risk band between monthly inspections.
- **Better instrument.** Inspection measurement noise σ 0.09 → 0.05 (the `deterioration` feature tracks hidden damage more faithfully).
- **Repairs are scheduled, not instant.** The trigger is unchanged (inspection day, measured deterioration > 0.63, probability 0.68), but a triggered repair is now queued and executed 2–14 days later — possessions must be scheduled, which is the premise of the whole system — while failure events still force an immediate repair. A queued repair is never double-queued, and a failure supersedes it.
- **Latent bundle bug fixed on the way.** In v2 the 30-day winner adopts isotonic recalibration, so the saved bundle contains a `_CalibratedModel` for the first time (no v1 winner ever adopted calibration). Built via `python -m`, it pickled as `__main__._CalibratedModel` and the planner server — a different `__main__` — could not have loaded `models.joblib` at all. Two guards in `pipeline.py` fix this: the class's `__module__` is pinned to `planner.maharashtra.pipeline`, and the module aliases itself under its package name in `sys.modules` so pickle resolves the same class object at dump time. Verified: a fresh server-style process loads the v2 bundle and `store.infer` reproduces stored risks exactly.

Verified outcome (identical code + seed, full rebuild in the sandbox; untouched test cohort of 9,600 rows; Mac retrain may select marginally different winners):

| Target | v1 (deployed) | v2 | v2 precision / recall / F1 @ 0.5 |
|---|---|---|---|
| 7-day risk | LR (engineered), AUC 0.6607, F1 0.000 | LR (engineered), AUC 0.8918 | 0.531 / 0.123 / 0.199 |
| 30-day risk | GB leaves 7 (engineered), AUC 0.6428, F1 0.000 | GB leaves 7 (engineered, isotonic-calibrated), AUC 0.8686 | 0.625 / 0.318 / 0.421 |
| 90-day risk | LR (poly), AUC 0.6069, F1 0.000 | RF tuned (engineered), AUC 0.8139 | 0.664 / 0.712 / 0.687 |
| Duration | XGBoost (engineered), MAE 9.34, R² 0.689 | XGBoost (engineered), MAE 8.85, R² 0.636 | buffer 17.04 min |

Counts: failure events 15,302 → 70,697; completed maintenance 74,450 → 108,636 (more failures force more emergency repairs, and queued repairs still execute). Horizon prevalences on the test cohort: 4.3% / 15.4% / 41.2%. All structural invariants hold: 500,000 requests, priority = sum of components, P(7) ≤ P(30) ≤ P(90) for every request, 12,000 pending.

**Honest framing.** These scores measure performance inside the v2 world, which was deliberately designed so that condition strongly determines failure. They are not evidence about real railways, and v1 → v2 is a change of world, not a model improvement — the two score sets are not comparable as modelling progress. Any document quoting v1 numbers (the presentation deck's results slides, the IEEE paper's results tables) must be regenerated from the v2 artifact before use. `VERSION` is now `mh-lifecycle-v2`; until the Mac retrains (`npm run data:maharashtra`), the shipped artifact describes the v1 bundle and the comparison tab shows the hash-mismatch banner.
