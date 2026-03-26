#!/usr/bin/env python3
"""
WQ Trend Monitor — Preprocessing Script
Reads water quality CSV → Statistical Engine → JSON Payload

Architecture: CSV → this script → payload.json → dashboard.html (loads externally)

Trigger rule: 2 last measurements strictly increasing (n5 >= 2), 5yr data only
"""

import csv
import json
import math
import os
import sys
from collections import defaultdict, Counter
from datetime import date, datetime

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
STANDARDS = {"TCEY": 10.0, "TECE": 10.0, "MTBE": 5.0}
PARAM_NAMES = {"TCEY": "TCE", "TECE": "PCE", "MTBE": "MTBE"}
EPSILON = 0.001
CUTOFF_5YR = date(2020, 1, 1)

DEFAULT_CSV = "היסטורית איכות מים לקידוחים - 3 פרמטרים הפקה בלבד.csv"
DEFAULT_OUT = "payload.json"


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------
def _sign(x):
    if x > 0:
        return 1
    if x < 0:
        return -1
    return 0


def _median(lst):
    if not lst:
        return 0.0
    s = sorted(lst)
    n = len(s)
    return (s[n // 2 - 1] + s[n // 2]) / 2.0 if n % 2 == 0 else s[n // 2]


def _normal_cdf(z):
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2)))


def _to_year(d):
    """Convert date to decimal year for slope calculations."""
    return d.year + (d.timetuple().tm_yday - 1) / 365.25


def _parse_date(s):
    s = s.strip()
    if not s:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


# ---------------------------------------------------------------------------
# Mann-Kendall (tie-corrected variance, continuity-corrected Z)
# ---------------------------------------------------------------------------
def mann_kendall(values, times):
    """
    Returns (tau, p_value, theil_sen_slope_per_year).
    Requires len >= 3.
    """
    n = len(values)
    if n < 3:
        return 0.0, 1.0, 0.0

    S = sum(
        _sign(values[j] - values[i])
        for i in range(n - 1)
        for j in range(i + 1, n)
    )

    tie_counts = Counter(values)
    tie_sum = sum(
        t * (t - 1) * (2 * t + 5)
        for t in tie_counts.values()
        if t > 1
    )
    var_s = (n * (n - 1) * (2 * n + 5) - tie_sum) / 18.0
    if var_s <= 0:
        return 0.0, 1.0, 0.0

    if S > 0:
        Z = (S - 1) / math.sqrt(var_s)
    elif S < 0:
        Z = (S + 1) / math.sqrt(var_s)
    else:
        Z = 0.0

    p = 2 * (1 - _normal_cdf(abs(Z)))

    slopes = [
        (values[j] - values[i]) / (times[j] - times[i])
        for i in range(n - 1)
        for j in range(i + 1, n)
        if (times[j] - times[i]) > 0
    ]
    ts_slope = _median(slopes)
    tau = S / (n * (n - 1) / 2) if n > 1 else 0.0

    return round(tau, 4), round(p, 6), round(ts_slope, 6)


# ---------------------------------------------------------------------------
# SNR (Signal-to-Noise Ratio)
# ---------------------------------------------------------------------------
def compute_snr(values, times, slope):
    """SNR = |slope| / residual_std using Theil-Sen line."""
    n = len(values)
    if n < 2:
        return 0.0
    intercept = _median([values[i] - slope * times[i] for i in range(n)])
    residuals = [values[i] - (slope * times[i] + intercept) for i in range(n)]
    mean_r = sum(residuals) / n
    var_r = sum((r - mean_r) ** 2 for r in residuals) / n
    std_r = math.sqrt(var_r) if var_r > 0 else 0.0
    if std_r == 0:
        return 10.0 if abs(slope) > 0 else 0.0
    return round(abs(slope) / std_r, 3)


def snr_band(snr):
    if snr >= 1.0:
        return "strong"
    if snr >= 0.3:
        return "moderate"
    return "weak"


# ---------------------------------------------------------------------------
# Pettitt Change-Point Test
# ---------------------------------------------------------------------------
def pettitt_test(values):
    """
    Returns (cp_index, p_value).
    cp_index is 1-based position within the series where shift occurs.
    """
    n = len(values)
    if n < 4:
        return None, 1.0

    # Build U series
    U = [0] * n
    for t in range(1, n):
        U[t] = U[t - 1] + sum(_sign(values[t] - values[j]) for j in range(t))

    candidates = range(1, n - 1)
    K = max(abs(U[t]) for t in candidates)
    if K == 0:
        return None, 1.0

    p = min(1.0, 2.0 * math.exp(-6.0 * K * K / (n ** 3 + n ** 2)))
    cp_idx = max(candidates, key=lambda t: abs(U[t]))
    return cp_idx, round(p, 6)


def _cp_direction(vals, cp_idx):
    """Compare median of 3 points before vs after the change point."""
    before = vals[max(0, cp_idx - 3): cp_idx]
    after = vals[cp_idx: min(len(vals), cp_idx + 3)]
    if not before or not after:
        return "FLAT"
    mb = _median(before)
    ma = _median(after)
    if mb == 0:
        return "UP" if ma > 0 else "FLAT"
    if ma > mb * 1.1:
        return "UP"
    if ma < mb * 0.9:
        return "DOWN"
    return "FLAT"


def find_change_point(adj_vals, dates):
    """
    Progressive tail search — stop at first significant result.
    Returns (cp_date_iso, direction, source) or (None, None, None).
    """
    n = len(adj_vals)
    ALPHA = 0.1

    def _try(vals_sub, dates_sub, label, require_second_half=False):
        if len(vals_sub) < 4:
            return None, None, None
        cp_idx, p = pettitt_test(vals_sub)
        if cp_idx is None or p >= ALPHA:
            return None, None, None
        if require_second_half and cp_idx < len(vals_sub) // 2:
            return None, None, None
        cpd = _cp_direction(vals_sub, cp_idx)
        return dates_sub[cp_idx].isoformat(), cpd, label

    # 1. 5yr window
    idx5 = [i for i, d in enumerate(dates) if d >= CUTOFF_5YR]
    if len(idx5) >= 4:
        cp, cpd, src = _try(
            [adj_vals[i] for i in idx5],
            [dates[i] for i in idx5],
            "5yr"
        )
        if cp:
            return cp, cpd, src

    # 2. Last third
    t3 = max(4, n // 3)
    cp, cpd, src = _try(adj_vals[n - t3:], dates[n - t3:], "last_third")
    if cp:
        return cp, cpd, src

    # 3. Second half
    half = max(4, n // 2)
    cp, cpd, src = _try(adj_vals[n - half:], dates[n - half:], "second_half")
    if cp:
        return cp, cpd, src

    # 4. Full series, only if CP falls in second half
    cp, cpd, src = _try(adj_vals, dates, "full_late", require_second_half=True)
    if cp:
        return cp, cpd, "full_late"

    return None, None, None


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------
def classify(pct, trend, trigger, cp_direction, years_to_std):
    """
    Returns "ALERT", "WATCH", or "OK".
    pct = last_conc / standard (not percentage, ratio).
    """
    cp_up = cp_direction == "UP"
    approaching = years_to_std is not None and years_to_std <= 5

    # ALERT conditions
    if pct >= 0.8 and (trend == "UP" or trigger):
        return "ALERT"
    if 0.3 <= pct < 0.8 and trend == "UP" and trigger:
        return "ALERT"
    if 0.3 <= pct < 0.8 and approaching:
        return "ALERT"

    # WATCH conditions
    if pct >= 0.8:
        return "WATCH"
    if 0.3 <= pct < 0.8 and (trend == "UP" or trigger or cp_up):
        return "WATCH"
    if pct < 0.3 and (trend == "UP" or trigger or cp_up):
        return "WATCH"

    return "OK"


def conc_tier(pct):
    if pct > 1.0:
        return "above_std"
    if pct >= 0.8:
        return "80_100"
    if pct >= 0.3:
        return "30_80"
    return "below_30"


# ---------------------------------------------------------------------------
# Per-(well, param) processing
# ---------------------------------------------------------------------------
def process_combo(well, param, records):
    """
    Records: list of dicts with keys: date, raw, adj, marker, basin.
    Returns stat dict or None (excluded).
    """
    standard = STANDARDS[param]
    records = sorted(records, key=lambda r: r["date"])

    dates = [r["date"] for r in records]
    raw_vals = [r["raw"] for r in records]
    adj_vals = [r["adj"] for r in records]
    markers = [r["marker"] for r in records]
    basin = records[0]["basin"]

    n = len(records)
    idx5 = [i for i, d in enumerate(dates) if d >= CUTOFF_5YR]
    n5 = len(idx5)

    # has_detection: any non-zero raw measurement (includes below-LOD reports of 0.2 <)
    has_detection = any(v > 0 for v in raw_vals)

    # Entry criteria
    if n < 4 or not has_detection or n5 < 1:
        return None

    times = [_to_year(d) for d in dates]
    last_raw = raw_vals[-1]
    last_adj = adj_vals[-1]
    last_date = dates[-1]
    max_conc = max(raw_vals)
    pct = last_adj / standard

    # ── Mann-Kendall full series (informational) ──────────────────────────
    tau_f, p_f, slope_f = mann_kendall(adj_vals, times)
    mf = {"t": round(tau_f, 4), "p": round(p_f, 6), "s": round(slope_f, 6)}

    # ── Mann-Kendall 5yr window (drives classification) ───────────────────
    m5 = None          # null in JSON when n5 < 3
    snr_val = 0.0
    trend = "NONE"
    slope_pct = 0.0

    if n5 >= 3:
        vals5 = [adj_vals[i] for i in idx5]
        times5 = [times[i] for i in idx5]
        tau5, p5, slope5 = mann_kendall(vals5, times5)
        snr_val = compute_snr(vals5, times5, slope5)
        band = snr_band(snr_val)
        m5 = {"t": round(tau5, 4), "p": round(p5, 6), "s": round(slope5, 6), "snr": round(snr_val, 3)}

        if band == "strong" and p5 < 0.1:
            trend = "UP" if slope5 > 0 else "DOWN"
        elif band == "moderate" and p5 < 0.05:
            trend = "UP" if slope5 > 0 else "DOWN"
        else:
            trend = "NONE"

        slope_pct = slope5 / standard * 100  # %std / year

    # ── Soft Trigger: last 2 measurements strictly increasing (5yr only) ──
    trigger = False
    if n5 >= 2:
        last_two_adj = [adj_vals[i] for i in idx5[-2:]]
        trigger = last_two_adj[1] > last_two_adj[0]

    # ── Years to threshold ────────────────────────────────────────────────
    years_to_std = None
    if m5 is not None and m5["s"] > 0 and pct < 1.0:
        remaining = (1.0 - pct) * standard  # µg/L to go
        years_to_std = round(remaining / m5["s"], 1)

    # ── Pettitt change-point ──────────────────────────────────────────────
    cp_date, cp_dir, cp_src = find_change_point(adj_vals, dates)

    # ── Classification ────────────────────────────────────────────────────
    alert = classify(pct, trend, trigger, cp_dir, years_to_std)
    ct = conc_tier(pct)

    return {
        "w": well,
        "p": param,
        "b": basin,
        "std": standard,
        "n": n,
        "n5": n5,
        "mx": round(max_conc, 4),
        "lc": round(last_raw, 4),
        "ld": last_date.isoformat(),
        "a": alert,
        "tr": trigger,
        "mf": mf,
        "m5": m5,
        "cp": cp_date,
        "cpd": cp_dir,
        "cps": cp_src,
        "pct": round(pct, 4),
        "spc": round(slope_pct, 4),
        "snr": round(snr_val, 3),
        "yt": years_to_std,
        "ct": ct,
        "trend": trend,
    }


# ---------------------------------------------------------------------------
# Data Loading
# ---------------------------------------------------------------------------
def load_records(filepath):
    """
    Returns dict: (well, param) → list of record dicts.
    Also returns ts_data: (well, param) → {dates, raw, adj, markers}.
    """
    combos = defaultdict(list)

    with open(filepath, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            well = row["שם קידוח"].strip()
            param = row["סמל פרמטר"].strip()
            basin = row["אגן"].strip()
            date_str = row["תאריך מדידה"].strip()
            marker = row.get("סמן", "").strip()

            if param not in STANDARDS:
                continue

            d = _parse_date(date_str)
            if d is None:
                continue

            try:
                raw = float(row["ריכוז"])
            except (ValueError, TypeError):
                continue

            # Censoring: '<' or 0 → epsilon
            if marker == "<" or raw == 0.0:
                adj = EPSILON
            else:
                adj = raw

            combos[(well, param)].append({
                "date": d,
                "raw": raw,
                "adj": adj,
                "marker": marker,
                "basin": basin,
            })

    return combos


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main(csv_path=None, out_path=None):
    csv_path = csv_path or DEFAULT_CSV
    out_path = out_path or DEFAULT_OUT

    if not os.path.isabs(csv_path):
        base = os.path.dirname(os.path.abspath(__file__))
        csv_path = os.path.join(base, csv_path)

    print(f"Loading: {csv_path}")
    combos = load_records(csv_path)
    print(f"  Found {len(combos)} (well, param) combinations")

    stats = []
    ts_data = {}
    excluded = 0

    for (well, param), records in combos.items():
        result = process_combo(well, param, records)
        if result is None:
            excluded += 1
            continue

        stats.append(result)

        # Build time-series entry (all records, sorted by date)
        recs_sorted = sorted(records, key=lambda r: r["date"])
        ts_data[f"{well}||{param}"] = {
            "dates": [r["date"].isoformat() for r in recs_sorted],
            "raw": [r["raw"] for r in recs_sorted],
            "adj": [round(r["adj"], 6) for r in recs_sorted],
            "markers": [r["marker"] for r in recs_sorted],
        }

    print(f"  Included: {len(stats)}  |  Excluded: {excluded}")

    # ── Counts ────────────────────────────────────────────────────────────
    counts = Counter(s["a"] for s in stats)
    counts_tier = Counter(s["ct"] for s in stats)

    # Unique wells (most severe alert across params)
    well_severity = {}
    _sev = {"ALERT": 2, "WATCH": 1, "OK": 0}
    for s in stats:
        w = s["w"]
        if w not in well_severity or _sev[s["a"]] > _sev[well_severity[w]]:
            well_severity[w] = s["a"]
    counts_unique = Counter(well_severity.values())

    # Unique wells by concentration tier (most severe tier per well)
    well_tier = {}
    _tier_sev = {"above_std": 3, "80_100": 2, "30_80": 1, "below_30": 0}
    for s in stats:
        w = s["w"]
        if w not in well_tier or _tier_sev[s["ct"]] > _tier_sev[well_tier[w]]:
            well_tier[w] = s["ct"]
    counts_unique_tier = Counter(well_tier.values())

    payload = {
        "meta": {
            "standards": STANDARDS,
            "paramNames": PARAM_NAMES,
            "epsilon": EPSILON,
            "generated": date.today().isoformat(),
        },
        "counts": dict(counts),
        "countsTier": dict(counts_tier),
        "countsUnique": dict(counts_unique),
        "countsUniqueTier": dict(counts_unique_tier),
        "stats": stats,
        "ts": ts_data,
    }

    # Ensure all tier/alert keys present (even if 0)
    for k in ("OK", "WATCH", "ALERT"):
        payload["counts"].setdefault(k, 0)
        payload["countsUnique"].setdefault(k, 0)
    for k in ("below_30", "30_80", "80_100", "above_std"):
        payload["countsTier"].setdefault(k, 0)
        payload["countsUniqueTier"].setdefault(k, 0)

    if not os.path.isabs(out_path):
        out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), out_path)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))

    size_kb = os.path.getsize(out_path) // 1024
    print(f"\nPayload written: {out_path} ({size_kb} KB)")
    print(f"  Combos: {len(stats)}")
    print(f"  ALERT:  {payload['counts'].get('ALERT', 0)}")
    print(f"  WATCH:  {payload['counts'].get('WATCH', 0)}")
    print(f"  OK:     {payload['counts'].get('OK', 0)}")
    print(f"\n  Unique wells: {sum(counts_unique.values())}")
    print(f"  ALERT:  {payload['countsUnique'].get('ALERT', 0)}")
    print(f"  WATCH:  {payload['countsUnique'].get('WATCH', 0)}")
    print(f"  OK:     {payload['countsUnique'].get('OK', 0)}")

    return payload


if __name__ == "__main__":
    csv_arg = sys.argv[1] if len(sys.argv) > 1 else None
    out_arg = sys.argv[2] if len(sys.argv) > 2 else None
    main(csv_arg, out_arg)
