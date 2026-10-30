# REFIT: data cleaning, washing-machine EDA and disaggregation

Take-home assignment for Flock Energy (Data Science). The repo covers four parts:

1. Clean the raw REFIT House 1 data to 1-minute resolution.
2. Explore washing-machine use across six households (official cleaned data).
3. Estimate washing-machine power from the household aggregate, evaluated on unseen homes.
4. Practical implications, limitations, and the effect of coarser smart-meter data.

Other documents:

| File | Content |
|---|---|
| `DATA.md` | Raw-data coverage and quality findings, cleaning steps, gap/outlier decisions, remaining concerns |
| `RESULTS.md` | Washing-machine EDA, model results, validation, failure analysis, interpretation |
| `Recommendation.md` | One-page utility-facing summary, plus the Section 4 answers |

## Summary of results

- **Task 1.** House 1 raw data (two files, about 8.5M rows) was cleaned to 920,031 one-minute rows (about 639 days). Short gaps (5 minutes or less) were interpolated and flagged. Longer outages were left empty and flagged, including a 41.6-day gap in early 2014 that the REFIT readme also notes. About 12.9% of aggregate minutes are in outages.
- **Task 2.** Six homes (Houses 1, 2, 4, 5, 7, 8, 9, 11, 15) with 555 to 648 days each and 243 to 835 detected washing cycles. Usage differs a lot between homes: House 7 starts washes mostly at 07:00 to 08:00, House 8 at night, Houses 5 and 9 mostly on weekends.
- **Task 3.** A gradient-boosted tree model, tested on two homes it never saw (Houses 2 and 8). It beats three baselines on RMSE by about 12% and 19%, and it finds most washes (recall 0.41 and 0.83 after thresholding). **Precision is low (about 0.25) and per-cycle energy is poorly recovered (median error about 70% to 78%).** I treat this as a coarse detector, not an accurate per-wash energy meter. See `Recommendation.md` for suitable and unsuitable uses.

## Repository layout

```
src/pipeline.py   cleaning pipeline: sort, dedupe, cap, resample to 1 min, gap handling (Task 1; reused by 2 and 3)
src/task2.py     washing-machine channel map, loader, cycle rule, availability scan
src/eda.py        Task 2: availability table, cycle plots, usage patterns, cross-house comparison
src/task3.py     Task 3: features, split, model, baselines, metrics, plots, feature importance
figures/          generated plots and metric tables
cache/            generated 1-minute data and detected cycles (not committed)
requirements.txt
```

## Setup

Python 3.10 or newer (developed on 3.12).

```
pip install -r requirements.txt
```

Download and extract both archives (they are `.7z`; use 7-Zip):

- Raw: `https://pureportal.strath.ac.uk/files/62090177/REFIT_RAW_081116.7z`
- Cleaned: `https://pureportal.strath.ac.uk/files/62090184/CLEAN_REFIT_081116.7z`

Data is not committed. The code expects `RAW_House1_Part1.csv` and `RAW_House1_Part2.csv` for Task 1 and `CLEAN_House<N>.csv` for Tasks 2 and 3.

## How to run

All commands run from the repo root.

**Task 1: clean raw House 1.** Pass Part 2 first, then Part 1, because the first file has priority where they overlap.
```
python src/pipeline.py "path/to/RAW_House1_Part2.csv" "path/to/RAW_House1_Part1.csv" --out house1_1min_clean.csv
```
This prints a log of rows dropped and minutes filled or flagged, and writes the 1-minute table with `*_filled` and `*_outage` flag columns.


**Task 2: washing-machine EDA** (cleaned data):
```
python src/eda.py "path/to/CLEAN_REFIT" --houses 1 2 5 7 8 9
```
Writes `figures/availability_table.csv`, `day_trace.png`, `cycles_detected.png`, `usage_patterns.png`, `cross_house_cycles.png`, and saves each house's detected cycles to `cache/`.

**Task 3: disaggregation:**
```
python src/disagg.py "path/to/CLEAN_REFIT" --train <TRAIN_HOUSES> --val 9 --test 2 8 --feat rich --explain
```
Replace `<TRAIN_HOUSES>` with the houses listed under "Validation design". Writes `figures/model_metrics_rich.csv`, `pred_vs_actual.png`, `failure_cases.png`, `model_vs_baselines.png` and `feature_importance.png`. Use `--feat basic` to reproduce the feature ablation. The first run caches 1-minute data for each house, so later runs are faster. Random seeds are fixed.

## Data used

- **Task 1:** raw House 1, files `RAW_House1_Part1.csv` and `RAW_House1_Part2.csv` (2013-10-09 to 2015-07-10).
- **Tasks 2 and 3:** the official cleaned data, Houses 1, 2, 5, 7, 8, 9 (plus the extra training homes listed below). Date ranges per house are in `figures/availability_table.csv`.
- **Sampling and units:** active power in watts, polled about every 6 to 8 seconds, resampled by us to 1 minute (mean).
- **Washing-machine channels** come from the dataset readme (for example House 1: `Appliance5`, House 2: `Appliance2`). Washer-dryer channels were excluded on purpose. The channel map is in `src/task2.py`.
- **Cleaned-data caveats** (from the dataset readme): sensor NaNs were forward-filled, and IAM spikes above 4000 W were replaced by zeros. The `Issues` flag marks minutes where the appliances sum to more than the aggregate. Those minutes are excluded from model training and scoring.

## Preprocessing decisions

1. **Time axis.** The Unix timestamp (UTC) is the index. The `Time` string is local time and is ambiguous at daylight-saving changes. That explains the backward timestamp jumps and a 3604 s "gap" in the raw data, which are not outages.
2. **Two raw files, one house.** Part 1 and Part 2 are two iterations of the same database that overlap in time. Part 1 writes 0 for unavailable sensors, while Part 2 writes NaN. Rows of Part 1 that fall inside Part 2's time span are dropped, so Part 2 wins. This matters because the two files are sampled at different instants, so de-duplicating on exact timestamps alone mixes Part 1's zeros into the minute means.
3. **Duplicates.** Rows with the same Unix timestamp: keep the first (after sorting by Unix and file priority).
4. **Extreme values.** Readings above 4000 W (appliance channels) or 15,000 W (aggregate) are set to NaN **before** resampling, so a single glitch such as 98,301 W cannot contaminate a 1-minute mean. The 4000 W cap follows the REFIT cleaning rule; the 15,000 W aggregate cap is my own choice.
5. **Gaps.** Runs of missing minutes of 5 or fewer are linearly interpolated and flagged. Longer runs are never filled and are flagged as outages. Leading and trailing gaps are never filled.
6. **Resampling.** Mean over each minute (power is a rate, so the mean is the right aggregation).

## Washing-machine cycle rule (Task 2)

A machine is "on" when it draws more than 10 W. Pauses of up to 10 minutes are bridged, because washers rest between wash, rinse and spin phases. An event counts as a cycle only if it lasts at least 10 minutes and uses at least 20 Wh. Cycles within 5 minutes of a sensor outage are dropped. Hours and weekdays use UK local time. The rule was checked visually on sample cycles from Houses 1 and 2 (`cycles_detected.png`).

## Validation design (Task 3)

- **Split by household.** Train on Houses **<TRAIN_HOUSES>**, validate on House 9, and test on Houses 2 and 8, which the model never sees. Houses are disjoint (the script asserts this). Splitting by home tests whether the model works in a new home, which is the realistic use, and avoids overlapping time windows crossing the split.
- **No leakage.** Features use only the aggregate, never the washer or any other appliance channel, because the target must be estimated from the whole-home meter alone. The hour-of-week baseline is fitted on the training homes only. The number of boosting iterations and the output threshold are chosen on the validation home only. Minutes with a missing washer or aggregate reading, or with `Issues = 1`, are excluded.
- **Model.** scikit-learn `HistGradientBoostingRegressor` (300 iterations maximum, learning rate 0.1, 31 leaves, L2 1.0), with the best iteration count chosen on House 9. Features are the aggregate at offsets from -30 to +30 minutes, centred rolling statistics over 5 to 61 minutes, the aggregate above its one-hour base load, past-only and future-only windows (10 to 120 minutes), and step-edge detectors. Hour and weekday were left out so the model does not memorise one household's habits. The washer is off 91 to 98% of the time, so training keeps every minute near a cycle plus 25% of the rest, with weight 4, to speed up training without shifting the average.
- **Output threshold ("gate").** Predictions at or below 25 W are set to 0. The threshold was picked on House 9 to maximise ON/OFF F1. Raw and gated results are both reported.
- **Baselines.** Always-off, the training-set mean, and an hour-of-week usage profile.
- **Metrics.** MAE and RMSE in watts, the total-energy error (SAE), minute-level ON/OFF precision, recall and F1 (on at 10 W), and the median per-cycle energy error. MAE alone is misleading here because always-off scores well on it, so RMSE, F1 and cycle energy are the metrics I rely on.

## Assumptions

- The washing-machine channel numbers from the dataset readme are correct for the whole period. House 13's machine changes in March 2015 and it is not used.
- A washing machine is the only thing the washer channel measures, and 10 W separates "on" from standby.
- 1-minute means keep enough shape for cycle detection. They smooth very short spikes, so peak power per cycle reads slightly lower than the raw 8-second peak.

## Trade-offs

- **Interpolation limit (5 minutes).** Longer limits fill more but invent data inside real outages.
- **Gradient boosting over a neural network.** Fast, handles missing values, easy to explain. A sequence model (Seq2Point) could use more context, but the data volume and the "complexity is not assessed" brief did not justify it.
- **Gating.** Raises precision and F1 on two of three homes but removes real energy, and it hurt F1 on House 2. Tuning the threshold on a single validation home is noisy.
- **Dropping `Issues = 1` minutes** removes inconsistent data but also removes a few genuine periods.

## Limitations (details in `RESULTS.md` and `Recommendation.md`)

Overlapping appliance signatures (kettle, dishwasher, dryer and heaters look like the washer's 2 kW heating burst), missing data (9 to 25% per home), household differences (machine power, cycle length, habits), only two unseen test homes, and applying UK results to India (different machines, no heating element in many Indian washers, water heaters, power cuts). With only 15 or 30-minute data a cycle becomes one to seven readings, so detection and per-cycle energy would be much harder.

## AI tools used

I used Claude (Anthropic) as a coding and reasoning assistant to plan the work, draft and debug the scripts, and draft explanations. I ran all the code on the real data, inspected the plots and metrics, and made the decisions about the houses, thresholds and what to report.

## Possible improvements

1. A two-stage model: an ON/OFF classifier plus a power regressor trained on on-minutes, with the classifier threshold chosen on several validation homes.
2. Cycle-level scoring: does a predicted wash overlap a real one? This is closer to how a utility would use it.
3. More homes and leave-one-house-out cross-validation, with a gate threshold chosen across several validation homes.
4. A false-alarm breakdown that checks which other appliance was running during each false alarm. This would only be used for analysis, never for training.
5. A sequence model (Seq2Point) and a comparison with XGBoost or LightGBM, with SHAP.
6. Re-validating on Indian smart-meter data before any deployment there.

