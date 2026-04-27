# CLAUDE.md — WQ Trend Monitor

## Project Overview

Hebrew RTL dashboard for monitoring contaminant trends in Israeli drinking water production wells.
Architecture: **Python preprocessing → JSON payload → standalone HTML/JS dashboard**

**Design principle:** Beautiful, custom Hebrew UI. Do NOT switch to Streamlit/Dash — they don't support RTL well.

---
1. Think Before Coding
Don't assume. Don't hide confusion. Surface tradeoffs.

Before implementing:

State your assumptions explicitly. If uncertain, ask.
If multiple interpretations exist, present them - don't pick silently.
If a simpler approach exists, say so. Push back when warranted.
If something is unclear, stop. Name what's confusing. Ask.
2. Simplicity First
Minimum code that solves the problem. Nothing speculative.

No features beyond what was asked.
No abstractions for single-use code.
No "flexibility" or "configurability" that wasn't requested.
No error handling for impossible scenarios.
If you write 200 lines and it could be 50, rewrite it.
Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

3. Surgical Changes
Touch only what you must. Clean up only your own mess.

When editing existing code:

Don't "improve" adjacent code, comments, or formatting.
Don't refactor things that aren't broken.
Match existing style, even if you'd do it differently.
If you notice unrelated dead code, mention it - don't delete it.
When your changes create orphans:

Remove imports/variables/functions that YOUR changes made unused.
Don't remove pre-existing dead code unless asked.
The test: Every changed line should trace directly to the user's request.

4. Goal-Driven Execution
Define success criteria. Loop until verified.

Transform tasks into verifiable goals:

"Add validation" → "Write tests for invalid inputs, then make them pass"
"Fix the bug" → "Write a test that reproduces it, then make it pass"
"Refactor X" → "Ensure tests pass before and after"
For multi-step tasks, state a brief plan:

1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.


---

## File Structure

```
preprocess.py           # Statistical engine: CSV → payload.json
wq_dashboard.html       # Dashboard: loads payload.json via fetch()
payload.json            # Generated data (do not edit manually)
CLAUDE.md               # This file

# Reference files (v6, do not modify):
payload_v6_final (1).json
wq_dashboard_v6 (1).html

# Source data:
היסטורית איכות מים לקידוחים - 3 פרמטרים הפקה בלבד.csv
```

---

## Running the Dashboard

```bash
# Step 1: Regenerate payload from CSV
python3 preprocess.py

# Step 2: Serve locally (required for fetch to work)
python3 -m http.server 8080
# Open: http://localhost:8080/wq_dashboard.html
```

---

## Statistical Engine (preprocess.py)

### Entry Criteria
```
n ≥ 4 AND has_detection AND n5 ≥ 1
has_detection = any raw > 0 (includes '<' below-LOD reports)
n5 = measurements since 2020-01-01
```

### Soft Trigger (CRITICAL — do not revert)
```python
# 2 last measurements strictly increasing (5yr data only)
trigger = (n5 >= 2) and (adj_vals[idx5[-1]] > adj_vals[idx5[-2]])
```
Previously 3 measurements. Changed intentionally. This causes more ALERT/WATCH classifications.

### Mann-Kendall
- Tie-corrected variance, continuity-corrected Z
- 5yr window (2020+): drives classification, requires n5 ≥ 3
- Full record: informational only (m5=null when n5 < 3)

### SNR Gating
```
SNR ≥ 1.0  → strong  → trust MK at p < 0.1
SNR 0.3–1.0 → moderate → trust MK at p < 0.05
SNR < 0.3  → weak   → trend = NONE
```

### Classification (4 tiers)
```
ALERT 🔴:
  ≥80% std + (trend UP or trigger)
  30–80% + (trend UP AND trigger)
  30–80% + approaching std (≤5 years)

WATCH 🟡:
  ≥80% std (no signals)
  30–80% + (trend UP or trigger or CP_UP)
  <30% + any signal

OK 🟢: everything else with data
Excluded: n5=0 or n<4 or no detections
```

### Standards (µg/L)
```python
STANDARDS = {"TCEY": 10.0, "TECE": 10.0, "MTBE": 5.0}
```
When adding new parameters: add to STANDARDS dict and rebuild payload.

---

## JSON Payload Structure

```json
{
  "meta": { "standards": {}, "paramNames": {}, "epsilon": 0.001, "generated": "date" },
  "counts": {"OK": N, "WATCH": N, "ALERT": N},
  "countsTier": {"below_30": N, "30_80": N, "80_100": N, "above_std": N},
  "countsUnique": {...},
  "countsUniqueTier": {...},
  "stats": [
    {
      "w": "well_name", "p": "PARAM", "b": "basin", "std": 10,
      "n": 14, "n5": 5,
      "mx": 2.67,     // max raw concentration
      "lc": 2.67,     // last raw concentration
      "ld": "2025-11-04",
      "a": "WATCH",   // alert tier
      "tr": true,     // soft trigger (2 rising)
      "mf": {"t": tau, "p": pval, "s": slope},       // full MK
      "m5": {"t": tau, "p": pval, "s": slope, "snr": snr}, // 5yr MK (null if n5<3)
      "cp": "2020-10-18", "cpd": "UP", "cps": "5yr",
      "pct": 0.267,   // last_adj / standard
      "spc": 4.33,    // slope %std/year
      "snr": 3.52,
      "yt": 16.9,     // years to threshold
      "ct": "below_30", "trend": "UP"
    }
  ],
  "ts": {
    "well||param": {
      "dates": [...], "raw": [...], "adj": [...], "markers": [...]
    }
  }
}
```

---

## Validated Reference Cases

| Well | Param | Expected | Key reason |
|------|-------|----------|------------|
| תא עיר מסילה | TCEY | 🔴 ALERT | SNR=2.67, catastrophic rise, CP_UP 2024 |
| נורדוי 5 | TECE | 🔴 ALERT | trigger + trend UP |
| בני ברק עיר ה | TCEY | 🔴 ALERT | trigger + trend UP, CP_UP |
| נתניה 42 | TCEY | 🔴 ALERT | trigger, approaching std |
| הרצליה ה | TCEY | 🔴 ALERT | trend UP, approaching std, CP_UP |
| רשלצ סיליקט | TECE | 🔴 ALERT | trigger, trend weak |
| בת ים 14 | TCEY | 🟡 WATCH | SNR=0.20, high but no signal |
| חרות גפן | TECE | 🟡 WATCH | CP_UP 2021 |
| אשדוד 31 | TCEY | 🟡 WATCH | trigger + trend UP + CP_UP |
| חולון 6 | TCEY | 🟢 OK | clean decline |

---

## Roadmap

### Phase 1 (next): UI Enhancements
- [ ] Aggregate charts: pie/donut — concentration tier distribution by basin and parameter
- [ ] Better time-series chart (Y-axis labels, grid, hover tooltip)
- [ ] Responsive layout improvements

### Phase 2 (planned): Excel — Contaminant Groups
- [ ] Input: Excel file with parameter → group mapping (fuel, PFAS, VOCs, metals, etc.)
- [ ] preprocess.py reads groups file, adds `"g": "group_name"` to each stat
- [ ] Dashboard: group filter dropdown, max-per-group trend display
- [ ] Each group has its own standards defined in the Excel

### Phase 2b: Monthly Production Data
- [ ] Input: Excel with well × month × m³ extraction volumes
- [ ] Shown as secondary axis on time-series charts
- [ ] Helps explain concentration trends (dilution, pumping rate effects)

### Phase 3: GIS & Infrastructure
- [ ] Map view (well markers colored by alert status)
- [ ] Well coordinates (lat/lon) in input data
- [ ] Nightly batch pipeline (CSV from org DB → payload.json → portal)
- [ ] Standards config in external JSON (for ~100+ future parameters)

### Phase 4: Advanced Analytics
- [ ] Post-CP trend analysis (MK on post-change-point segment)
- [ ] Suspicious data flagging (zeros amid high values, duplicates)
- [ ] Natural language summaries per well (Hebrew)
- [ ] Email/SMS alerts for new ALERT classifications

---

## Known Limitations

- All zeros and `<` values → ε=0.001. Actual LOD not stored.
- No seasonal adjustment.
- Pettitt unreliable on highly oscillating series.
- Wells with n5=1 or n5=2: classification by concentration + historical CP only (no MK).
- Trigger change (3→2) promotes some WATCH→ALERT on oscillating wells (e.g., ירושלים 1 TECE).

---

## Development Notes

- Always run `preprocess.py` after changing CSV or statistical logic
- After regenerating `payload.json`, reload dashboard in browser (hard refresh: Ctrl+Shift+R)
- To add a new parameter: add to `STANDARDS` dict in `preprocess.py`, rebuild payload
- The HTML references `payload.json` by relative path — keep both files in the same directory
- Git branch: `claude/water-quality-dashboard-spPEP`
