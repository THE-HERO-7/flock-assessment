"""Washing-machine helpers for the cleaned REFIT data (Task 2 / Task 3)."""
from os import mkdir
import sys
import pandas as pd
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

# Washing-machine channel per house, from the CLEAN_READ_ME appliance list.
# Houses 12 (none), 14 (not in dataset) omitted. House 4 has two machines (4 and 5);
# House 4 uses channel 4 here. Washer-dryers (H1 ch4, H8 ch3, H9 ch2, H18 ch4) excluded on purpose.
WM_CHANNEL = {1: 5, 2: 2, 3: 6, 4: 4, 5: 3, 6: 2, 7: 5, 8: 4, 9: 3, 10: 5, 11: 3,
              13: 3, 15: 3, 16: 5, 17: 4, 18: 5, 19: 2, 20: 4, 21: 3}

# Tentative cycle rule (to be checked against the plots)
ON_W, BRIDGE_MIN, MIN_DUR_MIN, MIN_WH = 10, 10, 10, 20


def load_house(data_dir, house):
    """Run the shared cleaning pipeline (src/pipeline.py) on a cleaned REFIT file and return a
    1-min frame [Aggregate, WM, Issues, WM_filled, WM_outage, Aggregate_outage] on a UTC index."""
    from pipeline import clean_refit
    ch = f"Appliance{WM_CHANNEL[house]}"
    out, _ = clean_refit([Path(data_dir) / f"CLEAN_House{house}.csv"], keep_cols=["Issues"])
    out = out.rename(columns={ch: "WM", f"{ch}_filled": "WM_filled", f"{ch}_outage": "WM_outage"})
    return out[["Aggregate", "WM", "Issues", "WM_filled", "WM_outage", "Aggregate_outage"]]


def detect_cycles(p, on_w=ON_W, bridge_min=BRIDGE_MIN, min_dur=MIN_DUR_MIN, min_wh=MIN_WH):
    """Active = power > on_w. Inactive stretches <= bridge_min between active minutes are bridged
    (washers pause between phases). Keep events lasting >= min_dur minutes and >= min_wh Wh."""
    a = (p > on_w).fillna(False)
    grp = (a != a.shift()).cumsum()
    runs = a.groupby(grp).agg(["first", "size"])
    inner = runs.index[(~runs["first"]) & (runs["size"] <= bridge_min)]
    inner = [g for g in inner if g not in (runs.index[0], runs.index[-1])]
    a2 = a | grp.isin(inner)
    ev = (a2 != a2.shift()).cumsum()[a2]
    rows = []
    for _, idx in p[a2].groupby(ev).groups.items():
        seg = p.loc[idx[0]:idx[-1]]
        rows.append({"start": idx[0], "end": idx[-1], "duration_min": len(seg),
                     "peak_w": seg.max(), "energy_wh": seg.fillna(0).sum() / 60})
    c = pd.DataFrame(rows, columns=["start", "end", "duration_min", "peak_w", "energy_wh"])
    return c[(c.duration_min >= min_dur) & (c.energy_wh >= min_wh)].reset_index(drop=True)


def scan(data_dir, houses=None):
    """Availability table used to pick houses."""
    rows = []
    for h in (houses or WM_CHANNEL):
        try:
            d = load_house(data_dir, h)
        except FileNotFoundError:
            continue
        valid = d["WM"].notna()
        agg_stuck = (d["Aggregate"].diff().abs() == 0)
        stuck_runs = agg_stuck.groupby((~agg_stuck).cumsum()).transform("sum")
        cyc = detect_cycles(d["WM"])
        rows.append({
            "house": h, "channel": WM_CHANNEL[h],
            "start": d.index[0].date(), "end": d.index[-1].date(),
            "days": (d.index[-1] - d.index[0]).days,
            "missing_pct": round(100 * (1 - valid.mean()), 1),
            "outage_pct": round(100 * d["WM_outage"].mean(), 1),
            "off_pct": round(100 * (d.loc[valid, "WM"] <= ON_W).mean(), 1),
            "stuck_agg_60min_pct": round(100 * (stuck_runs >= 60).mean(), 1),
            "issues_pct": round(100 * (d["Issues"] == 1).mean(), 1),
            "cycles": len(cyc),
            "cycles_per_week": round(len(cyc) / max((d.index[-1] - d.index[0]).days / 7, 1), 1),
            "median_wh": round(cyc.energy_wh.median(), 0) if len(cyc) else None,
        })
    return pd.DataFrame(rows).set_index("house")


if __name__ == "__main__":
    import sys
    t = scan(r"C:\Users\Asus\Desktop\clean")
    print(t.to_string())
    mkdir("figures")
    t.to_csv("figures/availability_scan.csv")