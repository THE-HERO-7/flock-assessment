"""Task 2 - washing-machine EDA on cleaned REFIT data.
Usage: python src/eda.py path/to/CLEAN_REFIT [--houses 1 2 5 7 8 9]
Outputs: figures/*.png, figures/availability_table.csv, cache/cycles_house<N>.csv
"""
import argparse
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from task2 import load_house, detect_cycles, ON_W, BRIDGE_MIN, MIN_DUR_MIN, MIN_WH

LOCAL_TZ = "Europe/London"           # REFIT homes are in the UK: hour/weekday use local time
FIG, CACHE = Path("figures"), Path("cache")
FIG.mkdir(exist_ok=True); CACHE.mkdir(exist_ok=True)


def get_house(data_dir, h):
    f = CACHE / f"house{h}_1min.pkl"
    if f.exists():
        return pd.read_pickle(f)
    d = load_house(data_dir, h)
    d.to_pickle(f)
    return d


def clean_cycles(d, pad_min=5):
    """Detect cycles, then drop any that sit next to a sensor outage (cannot trust their shape)."""
    c = detect_cycles(d["WM"])
    out = d["WM_outage"]
    pad = pd.Timedelta(minutes=pad_min)
    keep = [not out.loc[r.start - pad: r.end + pad].any() for r in c.itertuples()]
    c = c[keep].reset_index(drop=True)
    loc = c["start"].dt.tz_convert(LOCAL_TZ)
    c["hour"], c["dow"] = loc.dt.hour, loc.dt.dayofweek
    return c


def availability_row(h, d, c):
    valid = d["WM"].notna()
    run = (d["WM_outage"] != d["WM_outage"].shift()).cumsum()
    lens = d["WM_outage"].groupby(run).agg(["first", "size"])
    lens = lens[lens["first"]]["size"]
    days = (d.index[-1] - d.index[0]).days
    return {"house": h, "start": d.index[0].date(), "end": d.index[-1].date(), "days": days,
            "missing_pct": round(100 * (1 - valid.mean()), 1),
            "outages_gt_1h": int((lens > 60).sum()),
            "longest_outage_days": round(lens.max() / 1440, 1) if len(lens) else 0,
            "off_pct": round(100 * (d.loc[valid, "WM"] <= ON_W).mean(), 1),
            "issues_pct": round(100 * (d["Issues"] == 1).mean(), 1),
            "cycles": len(c), "cycles_per_week": round(len(c) / max(days / 7, 1), 1),
            "median_wh": round(c.energy_wh.median()), "median_min": round(c.duration_min.median())}


def plot_rule_check(data, cycles, houses, n=3):
    """Detected cycles overlaid on the 1-min trace; also the evidence for the rule."""
    fig, ax = plt.subplots(len(houses), n, figsize=(4.5 * n, 3 * len(houses)), squeeze=False)
    for i, h in enumerate(houses):
        c = cycles[h]
        order = c.sort_values("energy_wh").reset_index(drop=True)      # a small, medium and large cycle
        pick = order.iloc[[int(q * (len(order) - 1)) for q in (.25, .5, .75)]] if len(order) >= n else order
        for j, r in enumerate(pick.itertuples()):
            a = ax[i, j]
            w = data[h]["WM"].loc[r.start - pd.Timedelta(minutes=30): r.end + pd.Timedelta(minutes=30)]
            a.plot(w.index.tz_convert(LOCAL_TZ), w.values, lw=1)
            a.axvspan(r.start.tz_convert(LOCAL_TZ), r.end.tz_convert(LOCAL_TZ), color="orange", alpha=.25)
            a.axhline(ON_W, color="grey", ls=":", lw=.8)
            a.set_title(f"House {h}: {r.duration_min} min, {r.energy_wh:.0f} Wh", fontsize=9)
            a.tick_params(labelsize=7); a.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%H:%M"))
            if j == 0: a.set_ylabel("Washer (W)")
    fig.suptitle("Detected cycles (orange) on the 1-min washing-machine trace; dotted = on-threshold")
    fig.tight_layout(); fig.savefig(FIG / "cycles_detected.png", dpi=150); plt.close(fig)


def plot_day(data, cycles, h):
    """One representative local day: aggregate + washer. Day = median total cycle energy among clean days."""
    d, c = data[h], cycles[h]
    c = c.assign(day=c["start"].dt.tz_convert(LOCAL_TZ).dt.normalize())
    g = c.groupby("day").agg(n=("energy_wh", "size"), e=("energy_wh", "sum"))
    ok = []
    for day, r in g[g.n >= 2].iterrows():
        w = d.loc[day: day + pd.Timedelta(days=1)]
        if len(w) < 1400 or w["WM_outage"].any() or w["Aggregate_outage"].any():
            continue
        ok.append((day, r.e, (w["Issues"] == 1).mean()))
    if not ok:
        print(f"[plot_day] no outage-free day with >=2 cycles for house {h}"); return
    ok = [t for t in ok if t[2] <= 0.05] or ok      # prefer days with few 'Issues' minutes
    day = sorted(ok, key=lambda t: t[1])[len(ok) // 2][0]       # median total cycle energy
    w = d.loc[day: day + pd.Timedelta(days=1)]
    idx = w.index.tz_convert(LOCAL_TZ)
    fig, ax = plt.subplots(2, 1, figsize=(11, 5), sharex=True)
    ax[0].plot(idx, w["Aggregate"], lw=.8); ax[0].set_ylabel("Aggregate (W)")
    ax[1].plot(idx, w["WM"], lw=.8, color="tab:green"); ax[1].set_ylabel("Washing machine (W)")
    for r in c[(c.day == day)].itertuples():
        for a in ax: a.axvspan(r.start.tz_convert(LOCAL_TZ), r.end.tz_convert(LOCAL_TZ), color="orange", alpha=.2)
    ax[1].xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%H:%M"))
    fig.suptitle(f"House {h}, {day.date()} (local time): aggregate and washing machine; orange = detected cycles")
    fig.tight_layout(); fig.savefig(FIG / "day_trace.png", dpi=150); plt.close(fig)


def plot_patterns(cycles, houses):
    fig, ax = plt.subplots(1, 3, figsize=(16, 4))
    allc = pd.concat([cycles[h].assign(house=h) for h in houses])
    for h in houses:
        c = cycles[h]
        ax[0].plot(range(24), c.hour.value_counts(normalize=True).reindex(range(24), fill_value=0), label=f"H{h}")
        ax[1].plot(range(7), c.dow.value_counts(normalize=True).reindex(range(7), fill_value=0), marker="o", label=f"H{h}")
    ax[0].set(title="Cycle starts by hour of day (local)", xlabel="hour", ylabel="share of cycles"); ax[0].legend(fontsize=7)
    ax[1].set(title="Cycle starts by day of week", xticks=range(7)); ax[1].set_xticklabels(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])
    hm = allc.groupby(["dow", "hour"]).size().unstack(fill_value=0).reindex(index=range(7), columns=range(24), fill_value=0)
    im = ax[2].imshow(hm.values, aspect="auto", cmap="viridis"); fig.colorbar(im, ax=ax[2], label="cycles (all houses)")
    ax[2].set(title="All houses: weekday x hour", xlabel="hour", yticks=range(7)); ax[2].set_yticklabels(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])
    fig.tight_layout(); fig.savefig(FIG / "usage_patterns.png", dpi=150); plt.close(fig)


def plot_cross_house(cycles, houses):
    fig, ax = plt.subplots(1, 3, figsize=(15, 4))
    for a, (col, lab) in zip(ax, [("energy_wh", "Energy per cycle (Wh)"), ("duration_min", "Duration (min)"), ("peak_w", "Peak power (W)")]):
        a.boxplot([cycles[h][col] for h in houses], showfliers=False)
        a.set_xticklabels([f"H{h}" for h in houses]); a.set_title(lab)
    fig.suptitle("Cycle characteristics by house (outliers hidden)")
    fig.tight_layout(); fig.savefig(FIG / "cross_house_cycles.png", dpi=150); plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("data_dir"); ap.add_argument("--houses", type=int, nargs="+", default=[1, 2, 5, 7, 8, 9])
    a = ap.parse_args()
    data, cycles, rows = {}, {}, []
    for h in a.houses:
        data[h] = get_house(a.data_dir, h)
        cycles[h] = clean_cycles(data[h])
        cycles[h].to_csv(CACHE / f"cycles_house{h}.csv", index=False)
        rows.append(availability_row(h, data[h], cycles[h]))
    tab = pd.DataFrame(rows).set_index("house")
    tab.to_csv(FIG / "availability_table.csv"); print(tab.to_string())
    print(f"\nRule: active when washer > {ON_W} W; pauses <= {BRIDGE_MIN} min bridged; keep events >= {MIN_DUR_MIN} min and >= {MIN_WH} Wh; "
          "cycles within 5 min of a sensor outage dropped.")
    plot_rule_check(data, cycles, a.houses[:2])
    plot_day(data, cycles, a.houses[0])
    plot_patterns(cycles, a.houses)
    plot_cross_house(cycles, a.houses)
    print("figures saved in", FIG)


if __name__ == "__main__":
    main()