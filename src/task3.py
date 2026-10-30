"""Task 3 - estimate washing-machine power from the household aggregate (1-min).

Split by HOUSEHOLD (no house appears in two sets):   train / validation / test.
Features use the aggregate only (never the washer or other channels) in a centred window.
Usage: python src/disagg.py path/to/CLEAN_REFIT [--train 1 5 7] [--val 9] [--test 2 8]
Needs the 1-min caches written by eda.py (cache/house<N>_1min.pkl) or rebuilds them.
"""
import argparse
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

sys.path.insert(0, str(Path(__file__).parent))
from task2 import ON_W
from eda import get_house, clean_cycles, LOCAL_TZ, FIG, CACHE

OFFSETS = [-30, -15, -10, -5, -3, -2, -1, 1, 2, 3, 5, 10, 15, 30]    # minutes around t
WINDOWS = [5, 15, 31, 61]                                              # centred rolling windows


# ---------- features / data ----------
def _fwd(agg, w, stat):
    """Forward-looking rolling stat over the NEXT w minutes (window starts at t)."""
    r = agg[::-1].rolling(w, min_periods=max(1, int(0.7 * w)))
    return getattr(r, stat)()[::-1]


def make_features(agg, rich=True):
    f = {"agg": agg}
    for k in OFFSETS:
        f[f"agg_{k:+d}"] = agg.shift(-k)                                # aggregate at t+k
    for w in WINDOWS:
        r = agg.rolling(w, center=True, min_periods=max(1, int(0.7 * w)))
        f[f"mean{w}"], f[f"std{w}"], f[f"max{w}"], f[f"min{w}"] = r.mean(), r.std(), r.max(), r.min()
    f["above_base"] = agg - f["min61"]                                  # aggregate minus ~1 h base load
    f["d1"], f["d1_next"] = agg.diff(), agg.shift(-1) - agg
    if rich:       # asymmetric context (past vs future) and step-edge detectors: cycles last up to ~2 h
        for w in (10, 30, 60, 120):
            b = agg.rolling(w, min_periods=max(1, int(0.7 * w)))
            f[f"back_max{w}"], f[f"back_mean{w}"] = b.max(), b.mean()
            f[f"fwd_max{w}"], f[f"fwd_mean{w}"] = _fwd(agg, w, "max"), _fwd(agg, w, "mean")
        d = agg.diff()
        for w in (10, 30):
            f[f"back_dmax{w}"], f[f"back_dmin{w}"] = d.rolling(w, min_periods=1).max(), d.rolling(w, min_periods=1).min()
            f[f"fwd_dmax{w}"], f[f"fwd_dmin{w}"] = _fwd(d, w, "max"), _fwd(d, w, "min")
    return pd.DataFrame(f).astype("float32")                            # NaNs are fine for the model


RICH = True


def build(d):
    """Features, target and the mask of usable minutes (aggregate+washer present, Issues != 1)."""
    ok = d["Aggregate"].notna() & d["WM"].notna() & (d["Issues"] != 1)
    X = make_features(d["Aggregate"], RICH)
    return X[ok], d["WM"][ok].astype("float32")


def training_set(data, houses, neg_frac, rng):
    """Keep every minute near a cycle; keep neg_frac of the 'far from any cycle' minutes with weight 1/neg_frac."""
    Xs, ys, ws = [], [], []
    for h in houses:
        X, y = build(data[h])
        near = (y > ON_W).astype(float).rolling(21, center=True, min_periods=1).max() > 0
        keep = near | (rng.random(len(y)) < neg_frac)
        Xs.append(X[keep]); ys.append(y[keep]); ws.append(np.where(near[keep], 1.0, 1.0 / neg_frac))
    return pd.concat(Xs), pd.concat(ys), np.concatenate(ws)


# ---------- model + baselines ----------
def fit(Xtr, ytr, wtr, Xva, yva, max_iter=300):
    m = HistGradientBoostingRegressor(max_iter=max_iter, learning_rate=0.1, max_leaf_nodes=31, min_samples_leaf=50,
                                      l2_regularization=1.0, early_stopping=False, random_state=0)
    m.fit(Xtr, ytr, sample_weight=wtr)
    rmse = [np.sqrt(np.mean((np.clip(p, 0, None) - yva.values) ** 2)) for p in m.staged_predict(Xva)]
    return m, int(np.argmin(rmse)) + 1                                  # iterations chosen on the VALIDATION house


def predict(m, X, n):
    for i, p in enumerate(m.staged_predict(X), 1):
        if i == n:
            return pd.Series(np.clip(p, 0, None), index=X.index)


def perm_importance(m, n_it, X, y, n_rep=2, sample=100_000, seed=0):
    """Permutation importance on held-out data: rise in RMSE (W) when one feature is shuffled."""
    rng = np.random.default_rng(seed)
    idx = rng.choice(len(X), min(sample, len(X)), replace=False)
    Xs, ys = X.iloc[idx].reset_index(drop=True), y.iloc[idx].reset_index(drop=True)
    rmse = lambda p: float(np.sqrt(np.mean((p.values - ys.values) ** 2)))
    base = rmse(predict(m, Xs, n_it))
    out = {}
    for c in Xs.columns:
        inc = []
        for _ in range(n_rep):
            Xp = Xs.copy()
            Xp[c] = Xp[c].values[rng.permutation(len(Xp))]
            inc.append(rmse(predict(m, Xp, n_it)) - base)
        out[c] = float(np.mean(inc))
    return pd.Series(out).sort_values(ascending=False)


def plot_importance(imp, top=15):
    t = imp.head(top)[::-1]
    fig, ax = plt.subplots(figsize=(7, 5))
    ax.barh(t.index, t.values); ax.set_xlabel("RMSE increase when shuffled (W)")
    ax.set_title("Permutation importance (validation house)")
    fig.tight_layout(); fig.savefig(FIG / "feature_importance.png", dpi=150); plt.close(fig)


def week_key(idx):
    loc = idx.tz_convert(LOCAL_TZ)
    return pd.Series(loc.dayofweek * 24 + loc.hour, index=idx)


def baselines(y_train_all, y_test):
    prof = y_train_all.groupby(week_key(y_train_all.index)).mean()      # hour-of-week usage profile (train houses only)
    return {"always_off": pd.Series(0.0, index=y_test.index),
            "train_mean": pd.Series(float(y_train_all.mean()), index=y_test.index),
            "hour_of_week_profile": week_key(y_test.index).map(prof).fillna(0.0).astype(float)}


def tune_gate(y, p, grid=(10, 25, 50, 75, 100, 150, 200, 300, 400)):
    """Predictions below the threshold are set to 0. Threshold chosen to maximise ON/OFF F1 on the VALIDATION house."""
    best_t, best_f = grid[0], -1.0
    for t in grid:
        q = p.where(p > t, 0.0)
        tp = ((y > ON_W) & (q > 0)).sum()
        prec, rec = tp / max((q > 0).sum(), 1), tp / max((y > ON_W).sum(), 1)
        f1 = 2 * prec * rec / max(prec + rec, 1e-9)
        if f1 > best_f:
            best_t, best_f = t, f1
    return best_t


# ---------- metrics ----------
def metrics(y, p, cyc):
    on_t, on_p = y > ON_W, p > ON_W            # a prediction is "ON" above ON_W (raw output is gated separately)
    tp = (on_t & on_p).sum()
    prec, rec = tp / max(on_p.sum(), 1), tp / max(on_t.sum(), 1)
    errs = []
    for r in cyc.itertuples():
        t, q = y.loc[r.start:r.end].sum(), p.loc[r.start:r.end].sum()
        if t > 0:
            errs.append(abs(q - t) / t)
    return {"MAE_W": float(np.abs(p - y).mean()), "RMSE_W": float(np.sqrt(((p - y) ** 2).mean())),
            "SAE": float(abs(p.sum() - y.sum()) / y.sum()),             # total-energy error over the period
            "precision": float(prec), "recall": float(rec), "F1": float(2 * prec * rec / max(prec + rec, 1e-9)),
            "cycle_energy_err_median": float(np.median(errs)) if errs else np.nan}


# ---------- plots ----------
def pick_day(d, y, cyc):
    c = cyc.assign(day=cyc["start"].dt.tz_convert(LOCAL_TZ).dt.normalize())
    g = c.groupby("day").agg(n=("energy_wh", "size"), e=("energy_wh", "sum"))
    cand = []
    for n_min in (2, 1):
        for day, r in g[g.n >= n_min].iterrows():
            if len(y.loc[day: day + pd.Timedelta(days=1)]) >= 1296:    # day >=90% usable (a few Issues minutes are dropped)
                cand.append((day, r.e))
        if cand:
            break
    return sorted(cand, key=lambda t: t[1])[len(cand) // 2][0] if cand else None


def panel(ax_top, ax_bot, d, y, p, t0, t1, title):
    loc = lambda s: s.index.tz_convert(LOCAL_TZ)
    a, yy, pp = d["Aggregate"].loc[t0:t1], y.loc[t0:t1], p.loc[t0:t1]
    ax_top.plot(loc(a), a.values, lw=.7, color="grey"); ax_top.set_ylabel("Aggregate (W)"); ax_top.set_title(title, fontsize=9)
    ax_bot.plot(loc(yy), yy.values, lw=.9, color="tab:green", label="actual")
    ax_bot.plot(loc(pp), pp.values, lw=.9, color="tab:red", label="predicted")
    ax_bot.set_ylabel("Washing machine (W)"); ax_bot.legend(fontsize=7)
    for a in (ax_top, ax_bot):
        a.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%H:%M"))


def plot_pred_day(d, y, p, cyc, h):
    day = pick_day(d, y, cyc)
    if day is None:
        print(f"[plot] no usable day for house {h}"); return
    fig, ax = plt.subplots(2, 1, figsize=(11, 5), sharex=True)
    panel(ax[0], ax[1], d, y, p, day, day + pd.Timedelta(days=1), f"House {h} (unseen test home), {day.date()} local")
    fig.tight_layout(); fig.savefig(FIG / "pred_vs_actual.png", dpi=150); plt.close(fig)


def plot_failures(d, y, p, cyc, h):
    err = [(abs(p.loc[r.start:r.end].sum() - y.loc[r.start:r.end].sum()), r) for r in cyc.itertuples()
           if len(y.loc[r.start:r.end]) > 0]
    worst = max(err, key=lambda t: t[0])[1]                              # cycle with the largest energy error
    fp = (p * (y <= ON_W)).rolling(60, min_periods=1).sum()              # predicted energy while truly off
    t_fp = fp.idxmax()
    pad = pd.Timedelta(hours=2)
    fig, ax = plt.subplots(2, 2, figsize=(13, 5.5))
    mid = worst.start + (worst.end - worst.start) / 2
    panel(ax[0, 0], ax[1, 0], d, y, p, mid - pad, mid + pad, f"House {h}: worst-estimated cycle")
    panel(ax[0, 1], ax[1, 1], d, y, p, t_fp - pad, t_fp + pad, f"House {h}: strongest false alarm")
    fig.tight_layout(); fig.savefig(FIG / "failure_cases.png", dpi=150); plt.close(fig)


def explain(m, n_it, X, y, h, n=20000, seed=0):
    """Permutation importance on an unseen home: how much RMSE (W) rises when one feature is shuffled.
    Rows = every minute near a cycle plus an equal number of far-from-cycle minutes (a balanced sample)."""
    from sklearn.inspection import permutation_importance
    rng = np.random.default_rng(seed)
    near = ((y > ON_W).astype(float).rolling(21, center=True, min_periods=1).max() > 0).values
    a, b = np.where(near)[0], np.where(~near)[0]
    take = np.r_[rng.choice(a, min(len(a), n // 2), replace=False), rng.choice(b, min(len(b), n // 2), replace=False)]
    Xs, ys = X.iloc[take], y.iloc[take]

    def scorer(est, Xq, yq):
        return -float(np.sqrt(np.mean((predict(est, Xq, n_it).values - yq.values) ** 2)))

    r = permutation_importance(m, Xs, ys, scoring=scorer, n_repeats=3, random_state=seed, n_jobs=1)
    imp = pd.DataFrame({"feature": X.columns, "rmse_increase_W": r.importances_mean, "std": r.importances_std})
    imp = imp.sort_values("rmse_increase_W", ascending=False)
    imp.to_csv(FIG / f"permutation_importance_house{h}.csv", index=False)
    top = imp.head(15)[::-1]
    fig, ax = plt.subplots(figsize=(7, 5))
    ax.barh(top["feature"], top["rmse_increase_W"], xerr=top["std"])
    ax.set_xlabel("RMSE increase when shuffled (W)")
    ax.set_title(f"Top features, unseen House {h} (balanced sample)")
    fig.tight_layout(); fig.savefig(FIG / "feature_importance.png", dpi=150); plt.close(fig)
    print(imp.head(10).round(2).to_string(index=False))


def plot_bars(res):
    t = res.reset_index()
    fig, ax = plt.subplots(1, 3, figsize=(15, 4))
    for a, m in zip(ax, ["MAE_W", "SAE", "F1"]):
        t.pivot(index="method", columns="house", values=m).plot.bar(ax=a, rot=20); a.set_title(m)
    fig.tight_layout(); fig.savefig(FIG / "model_vs_baselines.png", dpi=150); plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("data_dir")
    ap.add_argument("--train", type=int, nargs="+", default=[1, 5, 7])
    ap.add_argument("--val", type=int, nargs="+", default=[9])
    ap.add_argument("--test", type=int, nargs="+", default=[2, 8])
    ap.add_argument("--neg_frac", type=float, default=0.25)
    ap.add_argument("--feat", choices=["basic", "rich"], default="rich")
    ap.add_argument("--explain", action="store_true", help="permutation feature importance on the first test house")
    a = ap.parse_args()
    assert not (set(a.train) & set(a.val)) and not (set(a.train) & set(a.test)) and not (set(a.val) & set(a.test)), "houses must not overlap"
    global RICH
    RICH = a.feat == "rich"
    rng = np.random.default_rng(0)
    data = {h: get_house(a.data_dir, h) for h in a.train + a.val + a.test}

    Xtr, ytr, wtr = training_set(data, a.train, a.neg_frac, rng)
    Xva, yva = build(data[a.val[0]]) if len(a.val) == 1 else map(pd.concat, zip(*[build(data[h]) for h in a.val]))
    m, n_it = fit(Xtr, ytr, wtr, Xva, yva)
    print(f"train rows {len(Xtr):,}; best iterations on validation house(s) {a.val}: {n_it}")

    imp = perm_importance(m, n_it, Xva, yva)
    imp.round(3).to_csv(FIG / f"feature_importance_{a.feat}.csv")
    plot_importance(imp)
    print("top features (RMSE increase, W):\n", imp.head(8).round(2).to_string())

    y_train_all = pd.concat([build(data[h])[1] for h in a.train])
    pv = predict(m, Xva, n_it)
    thr = tune_gate(yva, pv)
    print(f"output gate chosen on validation: predictions <= {thr} W set to 0")
    rows, keep = [], {}
    for h in a.val + a.test:
        d = data[h]
        X, y = build(d)
        cyc = clean_cycles(d)
        raw = predict(m, X, n_it)
        preds = {"model_hgb": raw, "model_hgb_gated": raw.where(raw > thr, 0.0), **baselines(y_train_all, y)}
        for name, p in preds.items():
            rows.append({"house": h, "split": "val" if h in a.val else "test", "method": name, **metrics(y, p, cyc)})
        keep[h] = (d, y, preds["model_hgb_gated"], cyc)
    res = pd.DataFrame(rows).set_index(["house", "method"])
    res.round(3).to_csv(FIG / f"model_metrics_{a.feat}.csv")
    print(res.round(3).to_string())

    h0 = a.test[0]
    plot_pred_day(*keep[h0][:4], h0)
    plot_failures(*keep[h0][:4], h0)
    plot_bars(res[res.split == "test"])
    if a.explain:
        Xe, ye = build(data[h0])
        explain(m, n_it, Xe, ye, h0)
    print("figures saved in", FIG)


if __name__ == "__main__":
    main()