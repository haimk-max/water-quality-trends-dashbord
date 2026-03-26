# WQ Trend Monitor — Project Specification
## Version 6 · 2026-03-25
## Input spec for Claude Code implementation

---

## Overview

Standalone Hebrew RTL dashboard for monitoring contaminant trends in Israeli drinking water production wells. Architecture: Python preprocessing → JSON payload → standalone HTML/JS dashboard. Designed for eventual integration into organizational GIS portal (nightly batch).

## Source Data

**File**: CSV, ~30K records, 1,954 wells, 3 parameters.
**Columns**: שם קידוח, סמל פרמטר, תאריך מדידה (MM/DD/YYYY), ריכוז, סמן ('<' = below LOD), אגן
**Parameters & Standards (µg/L)**: TCEY(TCE)=10, TECE(PCE)=10, MTBE=5
**Censoring**: All zeros and '<' values → ε=0.001 for statistical calculations. Raw values displayed in UI.
**Active wells**: 536 (at least 1 measurement since 2020-01-01)

---

## Statistical Engine

### Entry Criteria
```
n ≥ 4 AND has_detection AND n5 ≥ 1
Where n5 = count of measurements since 2020-01-01
Wells failing criteria are excluded from dashboard entirely.
```

### Mann-Kendall Trend Test
- Non-parametric, tie-corrected variance, continuity-corrected Z
- **5yr window (2020+)**: drives classification. Requires n5 ≥ 3.
- **Full record**: informational only (shown in UI, never drives alerts)

### SNR Gating (Signal-to-Noise Ratio)
```
SNR = |Theil-Sen_slope| / residual_std
SNR ≥ 1.0  → "strong"   → trust MK at p < 0.1
SNR 0.3–1.0 → "moderate" → trust MK at p < 0.05
SNR < 0.3   → "weak"     → trend = NONE (MK unreliable)
```

### Theil-Sen Slope
- Median of all pairwise slopes (robust to outliers)
- Normalized: `slope_pct = slope / standard * 100` (%std/year)
- Years-to-threshold: `(standard - last_conc) / slope` when slope > 0

### Soft Trigger
```python
trigger = (n5 >= 3) and (last > prev > prev_prev)  # on 5yr data only
```
Simple: are the last 3 measurements strictly increasing? No noise threshold.

### Pettitt Change-Point (Last CP)
```
Progressive tail search (stop at first significant):
  1. 5yr window (2020+)
  2. Last third of series
  3. Second half of series
  4. Full series (only if CP falls in second half)

p < 0.1 (relaxed for short series)
Keep the MOST RECENT (closest to end), not the most significant.
Direction: compare median(3 points before CP) vs median(3 points after CP)
  post > pre * 1.1 → "UP"
  post < pre * 0.9 → "DOWN"
  else → "FLAT"
CP_UP is an active signal for WATCH classification.
```

---

## Classification — 4 Tiers

```
ALERT (🔴):
  ≥80% of std + (trend UP or trigger)
  OR 30-80% + (trend UP AND trigger)
  OR 30-80% + approaching_std (≤5 years at current slope)

WATCH (🟡):
  ≥80% of std + no trend/trigger signals
  OR 30-80% + (trend UP or trigger or CP_UP) — but not combined enough for ALERT
  OR <30% + any signal (trend UP, trigger, or CP_UP)

OK (🟢):
  Everything else with sufficient data

Excluded:
  n5=0 or n<4 or no detections
```

### Current Distribution (536 unique wells)
- 🔴 ALERT: 8
- 🟡 WATCH: 38
- 🟢 OK: 490

### Concentration Tiers (trend-independent, for aggregate view)
- Above standard (>100%): 15
- 80–100%: 3
- 30–80%: 11
- Below 30%: 507

---

## Dashboard UI Specification

### Language & Layout
- Hebrew, RTL, font: Rubik (Google Fonts)
- Light theme (white/gray background, colored accents)
- Standalone HTML file, no build tools, no external frameworks

### Layout Structure (top to bottom)
1. **Header**: logo, title "ניטור מגמות איכות מים", parameter dropdown, basin dropdown, alert pills, "one row per well" checkbox, legend button
2. **Summary cards**: colored card per tier with count (🔴 8, 🟡 38, 🟢 490)
3. **Aggregate charts** (TODO): pie/donut charts showing concentration tier distribution, sliceable by basin and parameter
4. **Split view**: left = sortable table, right = time series charts (up to 6)

### Table Columns
| Column | Hebrew | Description |
|--------|--------|-------------|
| Status | סטטוס | Alert badge (colored) |
| Well | קידוח | Well name |
| Param | פרמטר | TCEY/TECE/MTBE |
| %Std | % מתקן | last_conc / standard (colored: red ≥80%, orange 30-80%, gray <30%) |
| Last | אחרון | Last concentration µg/L |
| Slope | שיפוע | %std/year (orange if +, blue if -) |
| SNR | SNR | Signal-to-noise (green ≥1, orange 0.3-1, gray <0.3) |
| Yrs→Std | שנים→תקן | Years to reach standard (red ≤5, orange ≤10) |
| Trigger | ⚡ | Lightning if last 3 rising |

### "One Row Per Well" Mode
In "All Parameters" view, show each well once with its most severe alert across all params. Checkbox toggle.

### Time Series Charts
- Canvas-drawn (no library)
- Data points: filled circle = detected, hollow = below LOD or zero
- Red dashed line = drinking water standard
- Orange dashed line = 30% of standard
- Purple dashed line = change-point (if detected)
- Light blue area fill under data line
- Y-axis: concentration, X-axis: years

### Legend Modal
Hebrew explanation of methodology, tiers, columns. Triggered by ℹ️ button.

---

## Payload JSON Structure

```json
{
  "meta": {
    "standards": {"TCEY": 10, "TECE": 10, "MTBE": 5},
    "paramNames": {"TCEY": "TCE", "TECE": "PCE", "MTBE": "MTBE"},
    "epsilon": 0.001
  },
  "counts": {"OK": 977, "WATCH": 51, "ALERT": 9},
  "countsUnique": {"OK": 490, "WATCH": 38, "ALERT": 8},
  "countsUniqueTier": {"below_30": 507, "30_80": 11, "above_std": 15, "80_100": 3},
  "stats": [
    {
      "w": "well name", "p": "TCEY", "b": "basin", "std": 10,
      "n": 14, "n5": 5,
      "mx": 2.67, "lc": 2.67, "ld": "2025-11-04",
      "a": "WATCH",
      "tr": true,
      "mf": {"t": -0.12, "p": 0.5, "s": -0.01},
      "m5": {"t": 1.0, "p": 0.001, "s": 0.43, "snr": 3.52},
      "cp": "2020-10-18", "cpd": "UP", "cps": "full_late",
      "pct": 0.267, "spc": 4.33, "snr": 3.52, "yt": 16.9,
      "ct": "below_30", "trend": "UP"
    }
  ],
  "ts": {
    "well||param": {
      "dates": ["2020-01-01", ...],
      "raw": [0.4, 1.68, ...],
      "adj": [0.4, 1.68, ...],
      "markers": ["", "<", ...]
    }
  }
}
```

---

## Validated Reference Cases

| Well | Param | Alert | %Std | Key Reason |
|------|-------|-------|------|------------|
| מסילה | TCEY | 🔴 | 5053% | SNR=2.67, catastrophic rise, CP_UP 2024 |
| נורדוי 5 | TECE | 🔴 | 774% | trigger + trend UP (SNR=0.59 moderate) |
| בני ברק עיר ה | TCEY | 🔴 | 425% | trigger + trend UP, CP_UP 2022 |
| אשדוד 24 | TCEY | 🔴 | 144% | trigger + trend UP, CP_UP |
| קרית אונו 3 | TECE | 🔴 | 130% | trend UP (noisy data, suspicious zeros) |
| סיליקט | TECE | 🔴 | 91% | trigger, trend weak (SNR=0.13) |
| נתניה 42 | TCEY | 🔴 | 78% | trigger, approaching std 1.4yr |
| הרצליה ה | TCEY | 🔴 | 74% | trend UP, approaching std 3.7yr, CP_UP |
| בת ים 14 | TCEY | 🟡 | 493% | SNR=0.20 → MK unreliable, high but no signal |
| ירושלים 1 | TECE | 🟡 | 1555% | oscillating, trend DOWN, high but declining |
| בני ברק ט | TCEY | 🟡 | 93% | high, declining trend |
| חרות גפן | TECE | 🟡 | 37% | CP_UP 2021 (new rise after remediation) |
| אשדוד 31 | TCEY | 🟡 | 27% | trigger, trend UP, CP_UP 2020, monotonic |
| חולון 6 | TCEY | 🟢 | 5% | SNR=1.72, clean decline, CP_DOWN |

---

## TODO — Prioritized

### Phase 1: Complete Current Dashboard
1. **Aggregate view above table**: pie/donut charts showing:
   - Concentration tier distribution (above std / 80-100% / 30-80% / below 30%)
   - Same breakdown per basin (stacked or small multiples)
   - Same breakdown per parameter
   - These are trend-independent — pure concentration snapshot
2. **Rebuild dashboard** with payload_v6_final.json (675KB, 1037 combos, 536 wells)
   - Current v6 dashboard still uses older smaller payload

### Phase 2: Data Enrichment
3. **Well operational status**: classify wells as producing / blended / decommissioned / monitoring
   - Requires external data or inference from sampling patterns
   - Affects dashboard filtering and presentation
4. **Contaminant groups**: when full parameter set arrives (~830 params), group by:
   - VOCs (current: TCE, PCE, MTBE)
   - Heavy metals
   - Nitrates
   - Pesticides
   - Other organics
   - Each group has its own standards and behavior patterns
5. **Well coordinates**: latitude/longitude for map integration
   - Enables spatial analysis (contamination plumes, proximity)

### Phase 3: GIS & Infrastructure
6. **Standards configuration file**: externalize drinking water standards to updatable JSON/CSV
   - Currently hardcoded: TCEY=10, TECE=10, MTBE=5
   - Full system will need ~100+ standards
7. **Map view**: integrate with organizational GIS portal
   - Wells as markers, colored by alert status
   - Click → time series popup
   - Contamination plume overlay potential
8. **Batch pipeline**: nightly cron → Python engine → JSON → portal update
   - Source data from organizational DB (not CSV)

### Phase 4: Advanced Analytics
9. **Post-CP trend analysis**: run MK on data segment after last change-point
   - Would catch trends that started mid-series (currently only 5yr window)
10. **Suspicious data flagging**: detect anomalous values
    - Zeros or near-zeros amid high-value series (e.g., Kiryat Ono: 0.1 amid 5-13)
    - Sudden jumps/drops suggesting measurement error
    - Duplicate timestamps with different values
11. **Friendlier UI layer**: simplified view for non-technical decision-makers
    - Traffic-light per well (no statistics)
    - Natural language summaries ("הקידוח הזה מראה עליית ריכוזים מתמשכת")
    - Email/SMS alerts for new ALERT classifications

### Known Limitations
- **Censoring simplification**: all zeros → ε, losing actual LOD values from '<' records
- **No seasonal adjustment**: some wells may have seasonal patterns
- **Pettitt on noisy data**: highly oscillating wells (e.g., Nordoy 5 TECE) resist CP detection in 5yr window
- **Single-observation wells**: n5=1 or n5=2 enter dashboard but have no MK/trigger capability — classification relies only on concentration + CP from historical data
