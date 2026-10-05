# 📈 Sports Science & Analytics Engines

StaminAI provides deterministic, scientifically-grounded sports analytics to monitor fitness, prevent overtraining, manage injury risks, and evaluate execution compliance.

---

## ⚡ Acute:Chronic Workload Ratio (ACWR)

The **Acute:Chronic Workload Ratio (ACWR)** monitors training load progression to keep athletes within the optimal adaptation zone while flagging injury risk spikes.

- **Acute Load**: The cumulative Relative Effort or TRIMP stress of the current rolling 7-day week.
- **Chronic Baseline**: The rolling unweighted average of the preceding `ACWR_CHRONIC_WEEKS` (default: 4 weeks).
  > [!IMPORTANT]
  > The current week is strictly excluded from its own chronic baseline to avoid dampening sudden spikes.
- **Minimum History**: Requires at least `ACWR_MIN_CHRONIC_WEEKS` (default: 2 weeks) of prior training before reporting a ratio; otherwise, reports `null` and zone `"unknown"`.

### Risk Zones
- **`low` (< 0.8)**: Under-training or taper; fitness decay if sustained.
- **`optimal` (0.8 – 1.3)**: Sweet spot; progressive fitness building with low injury incidence.
- **`overreaching` (1.3 – 1.5)**: Elevated fatigue; monitor recovery biometrics closely.
- **`spike` (> 1.5)**: High injury risk zone; rapid volume or intensity jump.

---

## 🏃 Run Durability & Injury Prevention Engine

Endurance athletes often have cardiovascular capacity that outpaces their musculoskeletal structural tolerance (tendons, bones, fascia). StaminAI's durability engine assesses run-specific load across five dimensions:

### 1. Week-over-Week Ramp Rate
Evaluates running mileage increase against `RUN_RAMP_SAFE_PCT` (default: 10% rule). Sudden jumps >15% trigger caution; >25% trigger high risk.

### 2. Run Spacing & Recovery Days
Tracks consecutive running days without rest and enforces `RUN_MIN_REST_DAYS` (default: 2 non-running days/week) needed for collagen synthesis and tissue remodeling.

### 3. Long Run Volume Share
Verifies that no single long run exceeds `RUN_LONG_RUN_MAX_SHARE` (default: 40%) of the entire week's running volume.

### 4. Foster Monotony & Strain
- **Training Monotony**: Mean daily load divided by standard deviation ($\mu / \sigma$). High monotony (>2.0) indicates uniform daily training without easy days.
- **Training Strain**: Weekly total load multiplied by Monotony ($\text{Load} \times \text{Monotony}$). High strain (>1500) strongly correlates with overtraining and upper respiratory illness.

### 5. Cross-Training Substitution
When running volume must be curtailed due to high risk or soreness, StaminAI converts the target aerobic stimulus into low-impact equivalents:
- **Cycling**: `BIKE_RUN_LOAD_FACTOR` (default: 0.55 run-equivalent stimulus per minute).
- **Aqua-Jogging**: `AQUA_JOG_LOAD_FACTOR` (default: 0.90 run-equivalent stimulus per minute).
- **Prescription**: Apportions non-impact aerobic conditioning (60% bike, 40% pool) paired with single-leg stability exercises.

---

## 🎯 80/20 Polarized Training & Karvonen HR Zones

StaminAI computes individual 5-zone Heart Rate Reserve boundaries using the **Karvonen formula**:

$$\text{Target HR} = \text{HR}_{\text{rest}} + (\text{HR}_{\text{max}} - \text{HR}_{\text{rest}}) \times \text{Intensity}\%$$

- **Zone 1 (Recovery)**: 50% – 60% HRR
- **Zone 2 (Endurance Base)**: 60% – 70% HRR
- **Zone 3 (Tempo)**: 70% – 80% HRR
- **Zone 4 (Threshold)**: 80% – 90% HRR
- **Zone 5 (VO2 Max / Anaerobic)**: 90% – 100% HRR

### Polarized Balance & The "Tempo Trap"
Weekly time is classified into:
- **Low Intensity (Z1–Z2)**: Target ~80%
- **Moderate Intensity (Z3)**: Target <10% (avoiding the common Zone 3 "grey zone" plateau)
- **High Intensity (Z4–Z5)**: Target 10–15%

---

## 📋 Plan vs. Actual Workout Compliance

StaminAI automatically parses natural Greek training prescriptions from Google Sheets (e.g., *"10χλμ ελεύθερο σε 5:20/χλμ"* or *"6x1000m σε 4:10/χλμ με 2' διάλειμμα"*) into structured targets:
- **Volume Delta**: Compares prescribed distance and duration against Strava execution.
- **Pace Fidelity**: Validates whether actual pace matched target interval speeds.
- **Compliance Score (0–100%)**: Weighted scoring evaluating execution discipline.

---

## ⛅ Weather-Adjusted Pacing Calculator

Environmental conditions dramatically alter metabolic cost:
- **Thermal Penalty**: Every 1°C increase above 15°C adds ~1.5 to 2.5 seconds/km to target pace to prevent cardiac drift and elevated core temperature.
- **Aerodynamic Wind Penalty**: Headwinds and crosswinds above 18 km/h add compensatory pacing adjustments based on frontal surface area resistance.

Athletes receive adjusted target paces in morning briefs so workouts elicit the intended metabolic stimulus without unintended strain.

---

## 👟 Gear & Running Shoe Mileage Wear

Running shoe foam (EVA/PEBA) loses resilience and mechanical cushioning between 600–800 km, increasing tibial shock and plantarfascitis risk:
- StaminAI tracks mileage per shoe model from Strava telemetry.
- Triggers replacement warning alerts when shoe mileage reaches `SHOE_ALERT_KM` (default: 650 km).
- Tracks bicycle component mileage and service intervals.

---

## ⏱️ Peter Riegel Race Predictor

Predicts race finish times for 5K, 10K, Half Marathon, and Marathon using the power formula:

$$T_2 = T_1 \times \left(\frac{D_2}{D_1}\right)^b$$

- **Dynamic Exponent Ladder**: Rather than a generic flat 1.06 exponent, StaminAI employs a distance-dependent fatigue ladder ($b \in [1.07, 1.10, 1.13, 1.145]$) calibrated against real-world endurance decay.
- **Dual Prediction Modes**:
  - **PB Mode**: Calibrated against official race results (`ATHLETE_PB_*`).
  - **Training Mode**: Calibrated against recent rolling training paces and current aerobic fitness.
