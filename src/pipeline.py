"""REFIT raw/cleaned -> 1-minute pipeline (any house; House 1 raw = two files).

Rules (all parameters are arguments, so they are documented in one place):
 1. Load Part2 + Part1 (pass paths in PRIORITY order, best first) and
    concatenate. The README says the two files are two iterations of the same
    database that overlap in time; Part2 marks unavailable sensors as NaN while
    Part1 writes 0, so Part2 wins wherever both have the same Unix timestamp.
 2. Time axis = Unix epoch (UTC). The 'Time' string is local time and
    ambiguous at DST changes, so it is only used for inspection.
 3. Sort by Unix (stable sort). Drop rows with unparseable Unix.
 4. Duplicate Unix timestamps: keep the first row after sorting by
    (Unix, file priority, original order).
 5. Impossible values (> cap, see CAPS) are set to NaN BEFORE resampling,
    so a single glitch (e.g. 98301 W) cannot contaminate a 1-minute mean.
 6. Resample to 1 min (mean). Minutes with no readings -> NaN.
 7. Gaps: NaN runs <= max_fill_min minutes are linearly interpolated (and
    flagged '<col>_filled'); longer runs are left NaN and flagged
    '<col>_outage'. Leading/trailing gaps are never filled.
"""
import pandas as pd

APPLIANCES = [f"Appliance{i}" for i in range(1, 10)]
POWER_COLS = ["Aggregate"] + APPLIANCES
CAPS = {"Aggregate": 15000, **{a: 4000 for a in APPLIANCES}}  # watts


def fill_short_gaps(s, max_len):
    """Fill NaN runs of length <= max_len only. Returns (series, filled_mask, outage_mask)."""
    isna = s.isna()
    run_id = (isna != isna.shift()).cumsum()
    run_len = isna.groupby(run_id).transform("sum")        # length of the NaN run each row sits in
    interp = s.interpolate(method="time", limit_area="inside")
    filled = isna & (run_len <= max_len) & interp.notna()
    outage = isna & (run_len > max_len)
    return s.where(~filled, interp), filled, outage


def clean_house1(paths, max_fill_min=5, caps=CAPS, keep_cols=()):
    # usage: clean_house1([part2_csv, part1_csv])   (raw House 1)
    #        clean_house1([CLEAN_HouseN.csv], keep_cols=["Issues"])   (cleaned data)
    log = {}
    frames = [pd.read_csv(p).assign(_prio=i) for i, p in enumerate(paths)]
    log["rows_raw"] = sum(len(f) for f in frames)

    # Overlap rule: the files are sampled at different instants, so exact-Unix
    # de-duplication cannot resolve the overlap and minute means would mix
    # Part1's 0s with Part2's NaNs. Instead drop every row of a lower-priority
    # file that falls inside the time span of a higher-priority file.
    kept, span = [], None
    for f in frames:
        f["Unix"] = pd.to_numeric(f["Unix"], errors="coerce")
        if span is not None:
            f = f[~f["Unix"].between(*span)]
        lo, hi = f["Unix"].min(), f["Unix"].max()
        span = (lo, hi) if span is None else (min(span[0], lo), max(span[1], hi))
        kept.append(f)
    df = pd.concat(kept, ignore_index=True)
    log["rows_dropped_overlap"] = log["rows_raw"] - len(df)

    df["Unix"] = pd.to_numeric(df["Unix"], errors="coerce")
    df = df.dropna(subset=["Unix"]).sort_values(["Unix", "_prio"], kind="stable")

    log["duplicates_removed"] = int(df["Unix"].duplicated().sum())
    df = df.drop_duplicates("Unix", keep="first")
    log["rows_after_dedup"] = len(df)

    df.index = pd.to_datetime(df["Unix"].astype("int64"), unit="s", utc=True)
    keep = list(keep_cols)
    flags = df[keep].resample("1min").max() if keep else None   # e.g. Issues: any flag in the minute
    df = df[POWER_COLS].astype("float64")

    log["extreme_set_nan"] = {}
    for c, cap in caps.items():
        bad = df[c] > cap
        log["extreme_set_nan"][c] = int(bad.sum())
        df.loc[bad, c] = float("nan")

    out = df.resample("1min").mean()
    if keep:
        out = out.join(flags)
    log["minutes_total"] = len(out)
    log["minutes_missing_before_fill"] = out.isna().sum().to_dict()

    for c in POWER_COLS:
        out[c], out[f"{c}_filled"], out[f"{c}_outage"] = fill_short_gaps(out[c], max_fill_min)
    log["minutes_filled"] = {c: int(out[f"{c}_filled"].sum()) for c in POWER_COLS}
    log["minutes_outage"] = {c: int(out[f"{c}_outage"].sum()) for c in POWER_COLS}
    return out, log


clean_refit = clean_house1   # generic name for non-House-1 use