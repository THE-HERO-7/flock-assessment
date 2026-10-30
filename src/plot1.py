"""Task 1 figures: raw House 1 before/after cleaning.
Usage: python src/task1_plots.py path/RAW_House1_Part2.csv path/RAW_House1_Part1.csv
Writes figures/house1_before_after.png, house1_spike_zoom.png, house1_outages.png,
       house1_intervals.png, house1_missing_by_channel.png
"""
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from pipeline import clean_house1, APPLIANCES, CAPS

FIG = Path("figures"); FIG.mkdir(exist_ok=True)
CHANNELS = [("Aggregate", "Aggregate"), ("Appliance1", "Appliance1 (fridge)"), ("Appliance5", "Appliance5 (washing machine)")]


def load_raw(paths):
    """Raw rows, sorted by Unix, duplicates KEPT (this is the 'before' data)."""
    raw = pd.concat([pd.read_csv(p) for p in paths], ignore_index=True)
    raw["Unix"] = pd.to_numeric(raw["Unix"], errors="coerce")
    raw = raw.dropna(subset=["Unix"]).sort_values("Unix", kind="stable")
    raw.index = pd.to_datetime(raw["Unix"].astype("int64"), unit="s", utc=True)
    return raw


def fmt(ax):
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b\n%H:%M"))
    ax.tick_params(labelsize=7)


def plot_before_after(raw, out, hours=36):
    f = out.index[out["Aggregate_filled"]]
    centre = f[len(f) // 10] if len(f) else out.index[len(out) // 2]       # a window that contains a filled gap
    t0, t1 = centre - pd.Timedelta(hours=hours / 2), centre + pd.Timedelta(hours=hours / 2)
    fig, ax = plt.subplots(3, 2, figsize=(14, 8), sharex=True)
    for i, (c, label) in enumerate(CHANNELS):
        r, o = raw[c].loc[t0:t1], out.loc[t0:t1]
        ax[i, 0].plot(r.index, r.values, lw=.5)
        ax[i, 1].plot(o.index, o[c].values, lw=.8)
        fl = o[o[f"{c}_filled"]]
        ax[i, 1].scatter(fl.index, fl[c], c="orange", s=10, zorder=3, label="interpolated minute")
        ax[i, 1].fill_between(o.index, 0, 1, where=o[f"{c}_outage"].values, transform=ax[i, 1].get_xaxis_transform(),
                              color="grey", alpha=.3, label="outage (not filled)")
        ax[i, 0].set_ylabel(f"{label} (W)", fontsize=8)
        ax[i, 1].sharey(ax[i, 0])
        fmt(ax[i, 0]); fmt(ax[i, 1])
    ax[0, 0].set_title("Before: raw readings (irregular, duplicates kept)")
    ax[0, 1].set_title("After: 1-minute cleaned"); ax[0, 1].legend(fontsize=7, loc="upper right")
    fig.suptitle("House 1, UTC"); fig.tight_layout(); fig.savefig(FIG / "house1_before_after.png", dpi=150); plt.close(fig)


def plot_spike(raw, out, cap=CAPS["Appliance1"]):
    apps = [a for a in APPLIANCES if a in raw]
    ch = raw[apps].max().idxmax()
    if raw[ch].max() <= cap:
        print("[spike] no raw appliance reading above the cap; skipped"); return
    t = raw[ch].idxmax(); pad = pd.Timedelta(hours=2)
    fig, ax = plt.subplots(1, 2, figsize=(12, 3.5), sharey=True)
    r = raw[ch].loc[t - pad:t + pad]
    ax[0].plot(r.index, r.values, lw=.8); ax[0].set_title(f"Before: raw {ch} (max {raw[ch].max():,.0f} W)")
    o = out[ch].loc[t - pad:t + pad]
    ax[1].plot(o.index, o.values, lw=.8); ax[1].set_title("After: reading above cap set to NaN, then 1-min mean")
    for a in ax:
        a.axhline(cap, color="red", ls=":", lw=.8); a.set_ylabel("W"); fmt(a)
    fig.tight_layout(); fig.savefig(FIG / "house1_spike_zoom.png", dpi=150); plt.close(fig)


def plot_outages(out):
    h = out["Aggregate"].resample("1h").mean()
    o = out["Aggregate_outage"].resample("1h").max().astype(bool)
    fig, ax = plt.subplots(figsize=(13, 3.5))
    ax.plot(h.index, h.values, lw=.5)
    ax.fill_between(h.index, 0, 1, where=o.values, transform=ax.get_xaxis_transform(), color="red", alpha=.3, label="outage (flagged, not filled)")
    ax.set_ylabel("Aggregate, hourly mean (W)"); ax.legend(fontsize=8)
    ax.set_title("House 1 aggregate over the whole period")
    fig.tight_layout(); fig.savefig(FIG / "house1_outages.png", dpi=150); plt.close(fig)


def plot_intervals(raw):
    d = np.diff(raw["Unix"].values)
    bins = [(0, 0, "0 s (duplicate)"), (1, 5, "1-5 s"), (6, 8, "6-8 s"), (9, 60, "9-60 s"), (61, 3600, "1-60 min"), (3601, 10**9, "over 1 h")]
    counts = [int(((d >= lo) & (d <= hi)).sum()) for lo, hi, _ in bins]
    fig, ax = plt.subplots(figsize=(7, 3.5))
    ax.bar([b[2] for b in bins], counts); ax.set_yscale("log"); ax.set_ylabel("number of intervals (log)")
    ax.set_title("Raw sampling intervals, House 1"); plt.setp(ax.get_xticklabels(), rotation=20, fontsize=8)
    fig.tight_layout(); fig.savefig(FIG / "house1_intervals.png", dpi=150); plt.close(fig)


def plot_missing(out):
    cols = ["Aggregate"] + APPLIANCES
    t = pd.DataFrame({"outage %": [100 * out[f"{c}_outage"].mean() for c in cols],
                      "filled %": [100 * out[f"{c}_filled"].mean() for c in cols]}, index=cols)
    ax = t.plot.bar(figsize=(9, 3.5), rot=30); ax.set_ylabel("% of minutes"); ax.set_title("Missing minutes per channel (after cleaning), House 1")
    plt.tight_layout(); plt.savefig(FIG / "house1_missing_by_channel.png", dpi=150); plt.close()


if __name__ == "__main__":
    paths=r'C:\Users\Asus\Desktop\raw\RAW_House1_Part2.csv', r'C:\Users\Asus\Desktop\raw\RAW_House1_Part1.csv'
    out, log = clean_house1(paths)
    raw = load_raw(paths)
    plot_before_after(raw, out); plot_spike(raw, out); plot_outages(out); plot_intervals(raw); plot_missing(out)
    print("figures saved in", FIG)