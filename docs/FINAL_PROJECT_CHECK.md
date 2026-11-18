# Final project check — 17 November 2026

Verified against saved model SHA-256 `96dd239dc868e160077e8a2af0d4962828c12112815f643cb0e6568f1584ca0a`. No UI changes, model retraining or persisted dataset regeneration were performed.

## Results

| Check | Observed result |
|---|---|
| `npm run test:planner` | 99 tests: 98 passed, 1 skipped; 166.807 seconds |
| Gantt time formatting | 8 passed, including negative offsets and day crossings |
| Worker shifts | 9 passed |
| Source import preservation | 6 passed |
| `npx tsc --noEmit` | Passed |
| `npm run lint` | Passed |
| `npm run build` | Passed; framework reports that some routes cannot be statically classified |
| In-process Controller integration | All 13 reported checkpoints passed; real route functions, authorization, SQLite persistence and Python handlers |
| Forced monthly solver recovery | Global `UNKNOWN` recovered as `ROLLING_FEASIBLE`; 26 tasks in 17 blocks, zero validation violations, persisted to disk |
| Saved-model metric reproduction | Passed on 9,600 risk observations and 1,378 completed interventions |
| Dataset checks | 500,000 requests, 12,000 assets, 576,000 observations, 74,450 completions; zero audited score or prediction/provenance violations |
| History feature parity | 15 observations matched training-grid features and production risk/duration inference, including early-history padding |
| Zone/department inference audit | 180 real stored requests checked across four zones and three departments; saved and re-inferred risks matched |
| Timetable catalogue | 53 selected references across 12 corridors; coverage remains incomplete |

The skipped delay test was `TestDelayEngine.test_scenario1_and_7_replan_moves_only_the_affected_block`: its selected allocation had no earlier train available to delay into that block. A focused rerun confirmed the same skip reason. This case is not counted as passed.

The in-process integration exercised login/password verification, anonymous/head/worker denial, invalid inputs, all-zone scoping, date and planning-period forwarding, current saved-model inference, digital-twin reinference and forged-prediction rejection, plan persistence/reload/approval/rejection, stale fingerprints, and competing revisions. It also read the model-comparison, models and operational routes with authorization. This does not verify HTTP transport or browser rendering.

## Current ML results

These are reproduced synthetic-test measurements, not operational railway accuracy.

| Horizon | Deployed estimator | ROC-AUC | Brier |
|---|---|---:|---:|
| 7 days | Logistic regression, history features | 0.6607 | 0.004967 |
| 30 days | Gradient boosting, history features | 0.6428 | 0.027331 |
| 90 days | Logistic regression, polynomial features | 0.6060 | 0.075978 |

All three have zero precision, recall and F1 at the reported 0.5 threshold. The risk estimates remain uncalibrated. The saved XGBoost duration model has MAE 9.34 minutes, RMSE 12.81 minutes, R² 0.6902, and an 18.08-minute validation residual buffer.

Sensitivity checks used all 9,600 test observations, varying one current input between its 10th and 90th percentiles while retaining recorded history and recomputing derived features. Mean 30-day risk changed by +0.9552 percentage points for deterioration and +0.7035 points for elapsed maintenance time. These hypothetical comparisons are not causal estimates and can fall outside the joint training distribution.

An existing isolated engine-level competition test selected `SYN-MH-0499069` at default weights (67.5 versus 65.9), then `SYN-MH-0491230` with a 100% failure-risk weight (4.4 versus 3.3). Both solves were `OPTIMAL` and passed validation. This test uses two stored model-scored requests with equal 202-minute buffered work durations and a synthetic one-slot closure; it is not a full-batch or HTTP experiment.

## Constraint checks

The all-zone audit deliberately uses bounded 28-request samples, not every pending request simultaneously.

| Zone | Status | Scheduled tasks | Blocks | Audit violations |
|---|---|---:|---:|---:|
| Z1 | OPTIMAL | 12 | 11 | 0 |
| Z2 | OPTIMAL | 6 | 6 | 0 |
| Z3 | FEASIBLE | 11 | 10 | 0 |
| Z4 | OPTIMAL | 13 | 12 | 0 |

All four zones scheduled zero work under synthetic full-horizon closures and zero crew capacity. The batch audit verified disjoint, exhaustive access to pending requests through the existing 300-request batches. `FEASIBLE` and `ROLLING_FEASIBLE` do not establish global optimality; independent batches and heuristic bundles are not a globally optimized combined plan.

## Corrections made

- Updated metric, sensitivity and calibration audits to honor each saved estimator's base, history-derived or polynomial feature contract. The old scripts supplied nine columns to models expecting more columns and failed.
- Updated the Controller audit to supply asset and observation identity during inference so it uses the correct stored inspection history.
- Added handling for asynchronous file-watcher errors in the development launcher. An injected `EMFILE` check confirmed that it warns and closes the watcher without crashing the services; normal shutdown still stops both children.
- Refreshed model, calibration and Controller audit JSON reports against the current model digest.

## Remaining blockers and limitations

- Live `test:control`, `test:local` and `test:operations` could not run: localhost HTTP access/binding fails with `EPERM` in this execution environment. Chrome independently returned `ERR_CONNECTION_REFUSED` for the Controller. No live-browser or HTTP pass is claimed.
- The training code decides whether to adopt probability calibration using test-cohort Brier/ECE. Although none of the current three calibration candidates was adopted, that test cohort was consulted for a model-selection decision. It must not be described as an entirely untouched independent final evaluation. Correcting that methodology and establishing a fresh independent evaluation would require separately authorized model work; existing models were preserved.
- Synthetic lifecycle outcomes do not establish real railway predictive accuracy. Timetable coverage omits unknown freight and other unlisted traffic. Candidate windows and allocations remain conditional simulations, not verified free track or operating clearance.

Reproducible checks: `npm run test:planner`, `node scripts/test-controller-inprocess.mjs`, `node scripts/test-planner-time.mjs`, `node scripts/test-shifts.mjs`, `node scripts/test-request-import.mjs`, `npx tsc --noEmit`, `npm run lint`, `npm run build`, and the `.venv/bin/python scripts/validate-*.py` audits described above. Run `validate-controller.py` before `validate-priority-recompute.py` to retain the competition result in the refreshed Controller audit.
