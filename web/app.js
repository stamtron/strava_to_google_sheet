// Endurance AI Dashboard — Frontend JavaScript State & Logic

let dashboardData = null;
let currentWeekKey = null;
let disciplineChart = null;
let recoveryChart = null;

// Progression Charts
let effortProgressionChart = null;
let volumeProgressionChart = null;
let elevationProgressionChart = null;
let acwrProgressionChart = null;

const DAY_NAMES_EN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const DAY_SHORT_EN = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"];

// Presentation for the ACWR zones emitted by the API (`acwr.zone`). Colours live
// here, not in the analytics layer, which only reports the machine-readable zone.
const ACWR_ZONE_STYLES = {
  low: { color: "#38bdf8", border: "rgba(56, 189, 248, 0.3)" },
  optimal: { color: "#10b981", border: "rgba(16, 185, 129, 0.3)" },
  overreaching: { color: "#f59e0b", border: "rgba(245, 158, 11, 0.3)" },
  spike: { color: "#ef4444", border: "rgba(239, 68, 68, 0.3)" },
  unknown: { color: "#94a3b8", border: "rgba(148, 163, 184, 0.3)" },
};

// Presentation for the severities and risk levels emitted by /api/durability.
// Same split as above: the analytics layer names the state, this file colours it.
const SEVERITY_STYLES = {
  none: { color: "#10b981", border: "rgba(16, 185, 129, 0.5)" },
  caution: { color: "#f59e0b", border: "rgba(245, 158, 11, 0.5)" },
  high: { color: "#ef4444", border: "rgba(239, 68, 68, 0.5)" },
  unknown: { color: "#94a3b8", border: "rgba(148, 163, 184, 0.4)" },
};

const RISK_STYLES = {
  low: { label: "Low risk", ...SEVERITY_STYLES.none },
  moderate: { label: "Moderate risk", ...SEVERITY_STYLES.caution },
  high: { label: "High risk", ...SEVERITY_STYLES.high },
  unknown: { label: "Not enough data", ...SEVERITY_STYLES.unknown },
};

const SIGNAL_LABELS = {
  run_ramp_rate: "Weekly ramp",
  run_rest_days: "Rest days",
  long_run_share: "Long-run share",
  training_monotony: "Monotony",
  training_strain: "Strain",
  run_acwr: "Run ACWR",
};

const MODALITY_LABELS = { aqua_jog: "🌊 Aqua jog", bike: "🚴 Bike" };

const EXERCISE_LABELS = {
  single_leg_calf_raise: "Single-leg calf raise",
  split_squat: "Split squat",
  step_down: "Step-down",
  single_leg_bridge: "Single-leg bridge",
  hip_abduction_side_lying: "Side-lying hip abduction",
  single_leg_balance_reach: "Single-leg balance reach",
};

let currentTriMode = "pb"; // "pb" or "train"

function getLocalDateString(d = new Date()) {
  const year = d.getFullYear();
  const month = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}
document.addEventListener("DOMContentLoaded", () => {
  initApp();
  setupEventListeners();
  initChatDrawer();
});

async function initApp() {
  try {
    const res = await fetch("/api/dashboard");
    if (!res.ok) throw new Error("Failed to load dashboard data");
    dashboardData = await res.json();

    populateWeekSelector();
    initCharts();
    initProgressionCharts();

    const weekKeys = Object.keys(dashboardData.weeks || {}).sort().reverse();
    if (weekKeys.length > 0) {
      selectWeek(weekKeys[0]);
    }

    renderPredictions(dashboardData.predictions);
    renderTriathlonPredictions(dashboardData.triathlon_pb || dashboardData.triathlon);
    renderWeatherOutlook(dashboardData.weather);
    updateProgressionCharts(dashboardData.progression);
  } catch (err) {
    console.error("Initialization error:", err);
    showToast("Failed to load dashboard data: " + err.message, "error");
  }

  // Outside the try: a separate endpoint on its own error path, so a durability
  // failure neither blanks the dashboard nor is masked by a dashboard failure.
  fetchDurability();
}

function setupEventListeners() {
  document.getElementById("weekSelector").addEventListener("change", (e) => {
    selectWeek(e.target.value);
  });

  document.getElementById("syncSheetBtn").addEventListener("click", handleSheetSync);
  document.getElementById("generateAiBtn").addEventListener("click", handleGenerateAiFeedback);
  document.getElementById("copyReportBtn").addEventListener("click", handleCopyReport);
  document.getElementById("sendWhatsAppBtn").addEventListener("click", handleSendWhatsApp);

  // AI Coach chat drawer
  document.getElementById("chatLauncher").addEventListener("click", () => toggleChatDrawer(true));
  document.getElementById("chatCloseBtn").addEventListener("click", () => toggleChatDrawer(false));
  document.getElementById("chatSendBtn").addEventListener("click", sendChatMessage);
  document.getElementById("chatResetBtn").addEventListener("click", resetChatSession);
  document.getElementById("chatMemoryBtn").addEventListener("click", toggleChatMemoryPanel);

  const chatInput = document.getElementById("chatInput");
  chatInput.addEventListener("keydown", (e) => {
    // Enter sends, Shift+Enter breaks the line: this is a chat box, not a form.
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendChatMessage();
    }
  });
  chatInput.addEventListener("input", () => {
    // Grow with the message up to the CSS max-height, then scroll internally.
    chatInput.style.height = "auto";
    chatInput.style.height = `${chatInput.scrollHeight}px`;
  });

  // Triathlon Mode Toggles
  const pbBtn = document.getElementById("triModePbBtn");
  const trainBtn = document.getElementById("triModeTrainBtn");
  if (pbBtn && trainBtn) {
    pbBtn.addEventListener("click", () => {
      currentTriMode = "pb";
      pbBtn.classList.add("active");
      trainBtn.classList.remove("active");
      if (dashboardData) {
        renderTriathlonPredictions(dashboardData.triathlon_pb || dashboardData.triathlon);
      }
    });

    trainBtn.addEventListener("click", () => {
      currentTriMode = "train";
      trainBtn.classList.add("active");
      pbBtn.classList.remove("active");
      if (dashboardData) {
        renderTriathlonPredictions(dashboardData.triathlon);
      }
    });
  }

  // Tab Navigation
  const tabWeekly = document.getElementById("tabWeeklyBtn");
  const tabProjections = document.getElementById("tabProjectionsBtn");
  const tabProgression = document.getElementById("tabProgressionBtn");
  const weeklyView = document.getElementById("weeklyView");
  const projectionsView = document.getElementById("projectionsView");
  const progressionView = document.getElementById("progressionView");

  function switchTab(target) {
    if (tabWeekly) tabWeekly.classList.toggle("active", target === "weekly");
    if (tabProjections) tabProjections.classList.toggle("active", target === "projections");
    if (tabProgression) tabProgression.classList.toggle("active", target === "progression");

    if (weeklyView) weeklyView.style.display = target === "weekly" ? "grid" : "none";
    if (projectionsView) {
      projectionsView.classList.toggle("active", target === "projections");
      projectionsView.style.display = target === "projections" ? "block" : "none";
    }
    if (progressionView) {
      progressionView.classList.toggle("active", target === "progression");
      progressionView.style.display = target === "progression" ? "block" : "none";
    }

    // Trigger chart resize if moving to Progression tab
    if (target === "progression") {
      setTimeout(() => {
        if (effortProgressionChart) effortProgressionChart.resize();
        if (volumeProgressionChart) volumeProgressionChart.resize();
        if (elevationProgressionChart) elevationProgressionChart.resize();
        if (acwrProgressionChart) acwrProgressionChart.resize();
      }, 50);
    }
  }

  if (tabWeekly) tabWeekly.addEventListener("click", () => switchTab("weekly"));
  if (tabProjections) tabProjections.addEventListener("click", () => switchTab("projections"));
  if (tabProgression) tabProgression.addEventListener("click", () => switchTab("progression"));

  // Sliders
  setupSlider("fatigueSlider", "fatigueVal", "/10");
  setupSlider("sorenessSlider", "sorenessVal", "/10");
  setupSlider("moodSlider", "moodVal", "/10");
}

function setupSlider(sliderId, labelId, suffix = "") {
  const slider = document.getElementById(sliderId);
  const label = document.getElementById(labelId);
  if (slider && label) {
    slider.addEventListener("input", (e) => {
      label.textContent = `${e.target.value}${suffix}`;
    });
  }
}

function populateWeekSelector() {
  const selector = document.getElementById("weekSelector");
  selector.innerHTML = "";

  const weekKeys = Object.keys(dashboardData.weeks || {}).sort().reverse();
  weekKeys.forEach((key, idx) => {
    const w = dashboardData.weeks[key];
    const mDate = new Date(w.week_monday + "T00:00:00");
    const sDate = new Date(w.week_sunday + "T00:00:00");
    const opt = document.createElement("option");
    opt.value = key;
    opt.textContent = `${mDate.toLocaleDateString("en-US", { month: "short", day: "numeric" })} – ${sDate.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })} ${idx === 0 ? "(Current)" : ""}`;
    selector.appendChild(opt);
  });
}

function selectWeek(weekKey) {
  currentWeekKey = weekKey;
  const weekData = dashboardData.weeks[weekKey];
  const garminData = (dashboardData.garmin || {})[weekKey] || null;

  if (!weekData) return;

  // 1. Update Metrics Cards
  renderMetricCards(weekData, garminData);

  // 2. Update 7-Day Calendar
  renderCalendar(weekData);

  // 3. Update Charts
  updateCharts(weekData, garminData);
}

function formatDuration(seconds) {
  if (!seconds || seconds <= 0) return "0m";
  const hrs = Math.floor(seconds / 3600);
  const mins = Math.floor((seconds % 3600) / 60);
  if (hrs > 0) return `${hrs}h ${mins}m`;
  return `${mins}m`;
}

function renderMetricCards(week, garmin) {
  document.getElementById("metricTotalHours").textContent = formatDuration(week.total_time_sec);
  document.getElementById("metricActivitiesCount").textContent = `${week.activities.length} workouts this week`;

  // Relative Effort & ACWR
  const effort = Math.round(week.total_relative_effort || 0);
  document.getElementById("metricRelativeEffort").textContent = effort;

  const acwr = week.acwr || {};
  const acwrBadge = document.getElementById("metricAcwrBadge");
  if (acwr.acwr_ratio) {
    acwrBadge.textContent = `ACWR: ${acwr.acwr_ratio} (${acwr.status ? acwr.status.split('(')[0].trim() : 'Active'})`;
    const zoneStyle = ACWR_ZONE_STYLES[acwr.zone] || ACWR_ZONE_STYLES.unknown;
    acwrBadge.style.color = zoneStyle.color;
    acwrBadge.style.borderColor = zoneStyle.border;
  } else {
    // No ratio yet (not enough chronic history) — show why instead of a stale value.
    acwrBadge.textContent = acwr.status
      ? `ACWR: — (${acwr.status.split('(')[0].trim()})`
      : "ACWR: —";
    acwrBadge.style.color = ACWR_ZONE_STYLES.unknown.color;
    acwrBadge.style.borderColor = ACWR_ZONE_STYLES.unknown.border;
  }

  document.getElementById("metricRunDist").textContent = `${week.run_dist_km.toFixed(2)} km`;
  document.getElementById("metricRunTime").textContent = formatDuration(week.run_time_sec);

  document.getElementById("metricBikeDist").textContent = `${week.bike_dist_km.toFixed(2)} km`;
  document.getElementById("metricBikeTime").textContent = formatDuration(week.bike_time_sec);

  document.getElementById("metricSwimDist").textContent = `${Math.round(week.swim_dist_m)} m`;
  document.getElementById("metricSwimTime").textContent = formatDuration(week.swim_time_sec);

  // Strength Training Card
  const strengthSec = week.strength_time_sec || 0;
  const strengthTimeEl = document.getElementById("metricStrengthTime");
  const strengthSessionsEl = document.getElementById("metricStrengthSessions");
  if (strengthTimeEl) strengthTimeEl.textContent = formatDuration(strengthSec);
  if (strengthSessionsEl) {
    const strengthActs = (week.activities || []).filter((a) => {
      const sp = (a.sport_type || a.type || "").toLowerCase();
      return sp.includes("weight") || sp.includes("strength") || sp.includes("crossfit") || sp.includes("workout");
    });
    const count = strengthActs.length;
    const strengthEffort = week.strength_relative_effort ? ` • 🔥 ${Math.round(week.strength_relative_effort)}` : "";
    strengthSessionsEl.textContent = `${count} session${count === 1 ? "" : "s"}${strengthEffort}`;
  }

  if (garmin && (garmin.total_sleep_h || garmin.avg_rhr || garmin.avg_hrv || garmin.avg_stress || garmin.avg_bb_charged !== null || garmin.avg_bb_drained !== null)) {
    document.getElementById("metricSleep").textContent = garmin.total_sleep_h ? `${garmin.total_sleep_h.toFixed(1)}h Sleep` : "--h Sleep";
    document.getElementById("metricRhr").textContent = garmin.avg_rhr ? `${garmin.avg_rhr} bpm` : "--";
    document.getElementById("metricHrv").textContent = garmin.avg_hrv ? `${garmin.avg_hrv} ms` : "--";
    
    const hasCharged = garmin.avg_bb_charged !== null && garmin.avg_bb_charged !== undefined;
    const hasDrained = garmin.avg_bb_drained !== null && garmin.avg_bb_drained !== undefined;
    const bbCharged = hasCharged ? `+${Math.round(garmin.avg_bb_charged)}` : "";
    const bbDrained = hasDrained ? `-${Math.round(garmin.avg_bb_drained)}` : "";
    const bbText = (bbCharged && bbDrained) ? `${bbCharged}/${bbDrained}` : (bbCharged || bbDrained || "--");
    document.getElementById("metricBodyBattery").textContent = bbText;
    document.getElementById("metricStress").textContent = garmin.avg_stress ? `${garmin.avg_stress}` : "--";
  } else {
    document.getElementById("metricSleep").textContent = "--h Sleep";
    document.getElementById("metricRhr").textContent = "--";
    document.getElementById("metricHrv").textContent = "--";
    document.getElementById("metricBodyBattery").textContent = "--";
    document.getElementById("metricStress").textContent = "--";
  }

  // 4. Update Polarized 80/20 Zone Bar
  renderPolarizedBar(week.polarized);
}

function renderPolarizedBar(pol) {
  if (!pol || pol.total_time_sec <= 0) {
    document.getElementById("polarizedStatusBadge").textContent = "No Data";
    document.getElementById("polarizedRatioText").textContent = "—";
    document.getElementById("polarizedBarLow").style.width = "0%";
    document.getElementById("polarizedBarMod").style.width = "0%";
    document.getElementById("polarizedBarHigh").style.width = "0%";
    document.getElementById("legendLowVal").textContent = "--";
    document.getElementById("legendModVal").textContent = "--";
    document.getElementById("legendHighVal").textContent = "--";
    return;
  }

  const lowPct = pol.low_pct || 0;
  const modPct = pol.moderate_pct || 0;
  const highPct = pol.high_pct || 0;

  document.getElementById("polarizedBarLow").style.width = `${lowPct}%`;
  document.getElementById("polarizedBarMod").style.width = `${modPct}%`;
  document.getElementById("polarizedBarHigh").style.width = `${highPct}%`;

  document.getElementById("legendLowVal").textContent = `${lowPct}%`;
  document.getElementById("legendModVal").textContent = `${modPct}%`;
  document.getElementById("legendHighVal").textContent = `${highPct}%`;

  const badge = document.getElementById("polarizedStatusBadge");
  const ratioText = document.getElementById("polarizedRatioText");

  ratioText.textContent = `${lowPct}% Low (Z1-Z2) • ${modPct}% Mid • ${highPct}% High`;

  if (pol.classification === "polarized") {
    badge.textContent = "🏆 80/20 Polarized Optimal";
    badge.style.color = "#10b981";
    badge.style.borderColor = "rgba(16, 185, 129, 0.4)";
  } else if (pol.classification === "pyramidal") {
    badge.textContent = "📈 Pyramidal Distribution";
    badge.style.color = "#00f2fe";
    badge.style.borderColor = "rgba(0, 242, 254, 0.4)";
  } else if (pol.classification === "moderate_trap") {
    badge.textContent = "⚠️ Zone 3 Tempo Trap";
    badge.style.color = "#f59e0b";
    badge.style.borderColor = "rgba(245, 158, 11, 0.4)";
  } else {
    badge.textContent = "Balanced Intensity";
    badge.style.color = "var(--text-secondary)";
    badge.style.borderColor = "var(--glass-border)";
  }
}

function formatPace(sport, movingTime, distanceMeters) {
  if (!movingTime || movingTime <= 0 || !distanceMeters || distanceMeters <= 0) return "";
  if (sport === "Swim") {
    const adjDistM = distanceMeters / 2.0;
    const paceSec = movingTime / (adjDistM / 100.0);
    const m = Math.floor(paceSec / 60);
    const s = Math.floor(paceSec % 60);
    return `${m}:${s.toString().padStart(2, "0")} /100m`;
  } else if (sport.includes("Ride")) {
    const speedKmh = (distanceMeters / 1000.0) / (movingTime / 3600.0);
    return `${speedKmh.toFixed(1)} km/h`;
  } else {
    const paceSec = movingTime / (distanceMeters / 1000.0);
    const m = Math.floor(paceSec / 60);
    const s = Math.floor(paceSec % 60);
    return `${m}:${s.toString().padStart(2, "0")} /km`;
  }
}

function renderCalendar(week) {
  const grid = document.getElementById("daysGrid");
  grid.innerHTML = "";

  const mDateStr = week.week_monday;
  const [mYear, mMonth, mDay] = mDateStr.split("-").map(Number);
  const mDate = new Date(mYear, mMonth - 1, mDay);
  const [sYear, sMonth, sDay] = week.week_sunday.split("-").map(Number);
  const sDate = new Date(sYear, sMonth - 1, sDay);

  const dateRangeEl = document.getElementById("calendarDateRange");
  dateRangeEl.textContent = `${mDate.toLocaleDateString("en-US", { month: "long", day: "numeric" })} – ${sDate.toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric" })}`;

  const todayStr = getLocalDateString();

  // Index weather items by date string YYYY-MM-DD
  const weatherMap = {};
  if (dashboardData && Array.isArray(dashboardData.weather)) {
    dashboardData.weather.forEach((w) => {
      if (w && w.date) {
        weatherMap[w.date] = w;
      }
    });
  }

  // Index activities by local date string YYYY-MM-DD
  const actsByDate = {};
  (week.activities || []).forEach((act) => {
    const actDateStr = (act.start_date_local || "").substring(0, 10);
    if (!actsByDate[actDateStr]) {
      actsByDate[actDateStr] = [];
    }
    actsByDate[actDateStr].push(act);
  });

  for (let i = 0; i < 7; i++) {
    const dayDate = new Date(mYear, mMonth - 1, mDay + i);
    const dateStr = getLocalDateString(dayDate);
    const isToday = dateStr === todayStr;

    const col = document.createElement("div");
    col.className = `day-column ${isToday ? "today" : ""}`;

    const header = document.createElement("div");
    header.className = "day-header";

    // Daily weather information
    const w = weatherMap[dateStr];
    let weatherHtml = "";
    if (w) {
      const maxTemp = w.temp_max_c !== null ? `${Math.round(w.temp_max_c)}°` : "";
      const minTemp = w.temp_min_c !== null ? `${Math.round(w.temp_min_c)}°` : "";
      const tempStr = (maxTemp && minTemp) ? `${maxTemp}/${minTemp}` : (maxTemp || "");
      const rainBadge = w.precipitation_mm > 0
        ? `💧${w.precipitation_mm.toFixed(1)}mm`
        : (w.precip_probability_pct > 0 ? `💧${w.precip_probability_pct}%` : "");
      const windInfo = w.wind_speed_max_kmh !== null ? ` • 💨 ${Math.round(w.wind_speed_max_kmh)} km/h` : "";
      const tooltip = `${w.condition || "Fair"}${windInfo}${w.precipitation_mm > 0 ? ` • Rain: ${w.precipitation_mm.toFixed(1)}mm` : ""}`;

      weatherHtml = `
        <div class="day-weather" title="${escapeHtml(tooltip)}">
          <span class="day-weather-icon">${escapeHtml(w.icon || "🌡️")}</span>
          <span class="day-weather-temp">${escapeHtml(tempStr)}</span>
          ${rainBadge ? `<span class="day-weather-rain">${escapeHtml(rainBadge)}</span>` : ""}
        </div>
      `;
    }

    header.innerHTML = `
      <div class="day-header-meta">
        <span class="day-name">${DAY_SHORT_EN[i]}</span>
        <span class="day-date">${dayDate.getMonth() + 1}/${dayDate.getDate()}</span>
        ${isToday ? `<span class="day-today-tag">TODAY</span>` : ""}
      </div>
      ${weatherHtml}
    `;
    col.appendChild(header);

    const acts = actsByDate[dateStr] || [];
    if (acts.length === 0) {
      const empty = document.createElement("div");
      empty.className = "day-rest-label";
      empty.textContent = "Rest";
      col.appendChild(empty);
    } else {
      acts.forEach((a) => {
        const item = document.createElement("div");
        item.className = "activity-item";

        const sport = a.sport_type || a.type || "";
        let sportClass = "act-run";
        let icon = "🏃";
        let distStr = "";

        const movingTime = a.moving_time || 0;
        const timeStr = formatDuration(movingTime);
        const rawDistM = a.distance || 0;

        if (sport.includes("Ride")) {
          sportClass = "act-ride";
          icon = "🚴";
          let distKm = (rawDistM / 1000.0);
          if (a.trainer && distKm < 0.1 && movingTime > 0) {
            distKm = (movingTime / 3600.0) * 21.0;
          }
          distStr = `${distKm.toFixed(2)} km`;
        } else if (sport.includes("Swim")) {
          sportClass = "act-swim";
          icon = "🏊";
          const distM = Math.round(rawDistM / 2.0);
          distStr = `${distM} m`;
        } else if (sport.includes("Weight") || sport.includes("Workout")) {
          sportClass = "act-strength";
          icon = "🏋️";
          distStr = "";
        } else {
          const distKm = (rawDistM / 1000.0).toFixed(2);
          distStr = `${distKm} km`;
        }

        const paceStr = formatPace(sport, movingTime, rawDistM);

        item.innerHTML = `
          <div class="act-type ${sportClass}">${icon} ${escapeHtml(a.name || sport)}</div>
          <div class="act-stat">⏱️ ${timeStr}${distStr ? ` • 📏 ${distStr}` : ""}</div>
          ${paceStr ? `<div class="act-stat">🏎️ ${paceStr}</div>` : ""}
          ${a.total_elevation_gain ? `<div class="act-stat">⛰️ +${Math.round(a.total_elevation_gain)}m</div>` : ""}
          ${a.average_heartrate ? `<div class="act-stat">❤️ Avg HR: ${Math.round(a.average_heartrate)} bpm</div>` : ""}
        `;
        col.appendChild(item);
      });
    }

    grid.appendChild(col);
  }
}

function initCharts() {
  const ctxDisc = document.getElementById("disciplineChart").getContext("2d");
  disciplineChart = new Chart(ctxDisc, {
    type: "doughnut",
    data: {
      labels: ["Running", "Cycling", "Swimming", "Strength"],
      datasets: [{
        data: [1, 1, 1, 1],
        backgroundColor: ["#f97316", "#00f2fe", "#10b981", "#b224ef"],
        borderWidth: 0,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          position: "bottom",
          labels: { color: "#94a3b8", font: { family: "Inter", size: 11 } },
        },
      },
      cutout: "70%",
    },
  });

  const ctxRec = document.getElementById("recoveryChart").getContext("2d");
  recoveryChart = new Chart(ctxRec, {
    type: "bar",
    data: {
      labels: ["Sleep (h)", "HRV (ms)", "Resting HR (bpm)"],
      datasets: [{
        label: "Garmin Health",
        data: [50, 65, 48],
        backgroundColor: ["#7928ca", "#00f2fe", "#ff0080"],
        borderRadius: 6,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        y: { grid: { color: "rgba(255, 255, 255, 0.05)" }, ticks: { color: "#94a3b8" } },
        x: { grid: { display: false }, ticks: { color: "#94a3b8" } },
      },
      plugins: { legend: { display: false } },
    },
  });
}

function initProgressionCharts() {
  // 1. Effort & Hours Progression
  const ctxEffort = document.getElementById("effortProgressionChart").getContext("2d");
  effortProgressionChart = new Chart(ctxEffort, {
    type: "bar",
    data: {
      labels: [],
      datasets: [
        {
          label: "Relative Effort",
          data: [],
          backgroundColor: "rgba(255, 0, 128, 0.65)",
          borderColor: "#ff0080",
          borderWidth: 1,
          borderRadius: 6,
          yAxisID: "yEffort",
        },
        {
          label: "Training Hours",
          data: [],
          type: "line",
          borderColor: "#00f2fe",
          backgroundColor: "rgba(0, 242, 254, 0.15)",
          borderWidth: 2,
          pointRadius: 4,
          fill: false,
          yAxisID: "yHours",
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        yEffort: {
          type: "linear",
          position: "left",
          grid: { color: "rgba(255, 255, 255, 0.05)" },
          ticks: { color: "#ff0080" },
          title: { display: true, text: "Relative Effort", color: "#ff0080" },
        },
        yHours: {
          type: "linear",
          position: "right",
          grid: { display: false },
          ticks: { color: "#00f2fe" },
          title: { display: true, text: "Hours", color: "#00f2fe" },
        },
        x: { grid: { display: false }, ticks: { color: "#94a3b8" } },
      },
      plugins: {
        legend: { labels: { color: "#94a3b8", font: { family: "Inter", size: 11 } } },
      },
    },
  });

  // 2. Volume Progression (Stacked Bar)
  const ctxVol = document.getElementById("volumeProgressionChart").getContext("2d");
  volumeProgressionChart = new Chart(ctxVol, {
    type: "bar",
    data: {
      labels: [],
      datasets: [
        { label: "Running (km)", data: [], backgroundColor: "#f97316", borderRadius: 4 },
        { label: "Cycling (km)", data: [], backgroundColor: "#00f2fe", borderRadius: 4 },
        { label: "Swimming (km)", data: [], backgroundColor: "#10b981", borderRadius: 4 },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        y: { stacked: true, grid: { color: "rgba(255, 255, 255, 0.05)" }, ticks: { color: "#94a3b8" } },
        x: { stacked: true, grid: { display: false }, ticks: { color: "#94a3b8" } },
      },
      plugins: {
        legend: { labels: { color: "#94a3b8", font: { family: "Inter", size: 11 } } },
      },
    },
  });

  // 3. Elevation Progression
  const ctxElev = document.getElementById("elevationProgressionChart").getContext("2d");
  elevationProgressionChart = new Chart(ctxElev, {
    type: "line",
    data: {
      labels: [],
      datasets: [{
        label: "Elevation Gain (m)",
        data: [],
        borderColor: "#b224ef",
        backgroundColor: "rgba(178, 36, 239, 0.2)",
        fill: true,
        tension: 0.3,
        borderWidth: 2,
        pointRadius: 4,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        y: { grid: { color: "rgba(255, 255, 255, 0.05)" }, ticks: { color: "#94a3b8" } },
        x: { grid: { display: false }, ticks: { color: "#94a3b8" } },
      },
      plugins: {
        legend: { labels: { color: "#94a3b8", font: { family: "Inter", size: 11 } } },
      },
    },
  });

  // 4. ACWR Chart
  const ctxAcwr = document.getElementById("acwrProgressionChart").getContext("2d");
  acwrProgressionChart = new Chart(ctxAcwr, {
    type: "line",
    data: {
      labels: [],
      datasets: [
        {
          label: "ACWR Ratio (Sweet Spot: 0.8 - 1.3)",
          data: [],
          borderColor: "#10b981",
          borderWidth: 2,
          pointRadius: 5,
          pointBackgroundColor: "#10b981",
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        y: {
          min: 0.5,
          max: 2.0,
          grid: { color: "rgba(255, 255, 255, 0.05)" },
          ticks: { color: "#94a3b8" },
        },
        x: { grid: { display: false }, ticks: { color: "#94a3b8" } },
      },
      plugins: {
        legend: { labels: { color: "#94a3b8", font: { family: "Inter", size: 11 } } },
      },
    },
  });
}

function updateCharts(week, garmin) {
  if (disciplineChart) {
    const runH = week.run_time_sec / 3600.0;
    const bikeH = week.bike_time_sec / 3600.0;
    const swimH = week.swim_time_sec / 3600.0;
    const strengthH = week.strength_time_sec / 3600.0;

    disciplineChart.data.datasets[0].data = [runH, bikeH, swimH, strengthH];
    disciplineChart.update();
  }

  if (recoveryChart && garmin) {
    recoveryChart.data.datasets[0].data = [
      garmin.total_sleep_h || 0,
      garmin.avg_hrv || 0,
      garmin.avg_rhr || 0,
    ];
    recoveryChart.update();
  }
}

function updateProgressionCharts(progression) {
  if (!progression || progression.length === 0) return;

  const labels = progression.map(p => p.label);
  const efforts = progression.map(p => p.relative_effort);
  const hours = progression.map(p => p.total_hours);
  const runs = progression.map(p => p.run_km);
  const bikes = progression.map(p => p.bike_km);
  const swims = progression.map(p => p.swim_km);
  const elevs = progression.map(p => p.elevation_m);
  // null (not 1.0) for weeks without enough chronic history: Chart.js renders a
  // gap there instead of inventing a neutral ratio.
  const acwrs = progression.map(p => (p.acwr && p.acwr.acwr_ratio) || null);

  if (effortProgressionChart) {
    effortProgressionChart.data.labels = labels;
    effortProgressionChart.data.datasets[0].data = efforts;
    effortProgressionChart.data.datasets[1].data = hours;
    effortProgressionChart.update();
  }

  if (volumeProgressionChart) {
    volumeProgressionChart.data.labels = labels;
    volumeProgressionChart.data.datasets[0].data = runs;
    volumeProgressionChart.data.datasets[1].data = bikes;
    volumeProgressionChart.data.datasets[2].data = swims;
    volumeProgressionChart.update();
  }

  if (elevationProgressionChart) {
    elevationProgressionChart.data.labels = labels;
    elevationProgressionChart.data.datasets[0].data = elevs;
    elevationProgressionChart.update();
  }

  if (acwrProgressionChart) {
    acwrProgressionChart.data.labels = labels;
    acwrProgressionChart.data.datasets[0].data = acwrs;
    acwrProgressionChart.update();
  }
}

function renderPredictions(predData) {
  if (!predData || !predData.predictions) return;

  const tbody = document.getElementById("predictionsBody");
  tbody.innerHTML = "";

  document.getElementById("basePaceBadge").textContent = `Base: ${predData.base_pace_used}`;

  predData.predictions.forEach((p) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><strong>${p.name}</strong></td>
      <td>${p.predicted_time}</td>
      <td>${p.predicted_pace}</td>
    `;
    tbody.appendChild(tr);
  });
}

function renderTriathlonPredictions(triData) {
  const data = triData || (currentTriMode === "pb" ? (dashboardData.triathlon_pb || dashboardData.triathlon) : dashboardData.triathlon);
  if (!data || !data.predictions) return;

  const container = document.getElementById("triathlonContainer");
  container.innerHTML = "";

  const isPb = data.mode === "race_pb" || currentTriMode === "pb";
  if (data.baselines) {
    const prefix = isPb ? "🏆 Race PB Pace:" : "📈 Training Base:";
    document.getElementById("triBaselinesBadge").textContent = `${prefix} 🏊 ${data.baselines.swim_100m} • 🚴 ${data.baselines.bike_speed} • 🏃 ${data.baselines.run_pace}`;
  }

  data.predictions.forEach((p) => {
    const card = document.createElement("div");
    card.className = "triathlon-card";

    const s = p.splits;
    card.innerHTML = `
      <div class="tri-header">
        <div class="tri-title">🏁 ${p.name}</div>
        <div class="tri-total-time">${p.total_time}</div>
      </div>
      <div class="tri-splits-grid">
        <div class="tri-split-box">
          <div class="tri-split-label">🏊 Swim (${s.swim.distance})</div>
          <div class="tri-split-time">${s.swim.time}</div>
          <div class="tri-split-pace">${s.swim.pace}</div>
        </div>
        <div class="tri-split-box">
          <div class="tri-split-label">⚡ T1</div>
          <div class="tri-split-time">${s.t1}</div>
          <div class="tri-split-pace">Transition</div>
        </div>
        <div class="tri-split-box">
          <div class="tri-split-label">🚴 Bike (${s.bike.distance})</div>
          <div class="tri-split-time">${s.bike.time}</div>
          <div class="tri-split-pace">${s.bike.speed}</div>
        </div>
        <div class="tri-split-box">
          <div class="tri-split-label">⚡ T2</div>
          <div class="tri-split-time">${s.t2}</div>
          <div class="tri-split-pace">Transition</div>
        </div>
        <div class="tri-split-box">
          <div class="tri-split-label">🏃 Run (${s.run.distance})</div>
          <div class="tri-split-time">${s.run.time}</div>
          <div class="tri-split-pace">${s.run.pace}</div>
        </div>
      </div>
    `;
    container.appendChild(card);
  });
}

function renderWeatherOutlook(weatherList) {
  const container = document.getElementById("weatherGrid");
  if (!container) return;

  if (!weatherList || weatherList.length === 0) {
    container.innerHTML = `<div style="grid-column: 1/-1; text-align: center; color: var(--text-muted); padding: 12px; font-size: 0.82rem;">Athens weather forecast currently unavailable.</div>`;
    return;
  }

  const todayStr = getLocalDateString();

  const html = weatherList.map((day) => {
    const isToday = day.date === todayStr;
    const d = new Date(day.date + "T00:00:00");
    const dayName = isToday ? "Today" : d.toLocaleDateString("en-US", { weekday: "short" });
    const dateFormatted = d.toLocaleDateString("en-US", { month: "short", day: "numeric" });

    const maxTemp = day.temp_max_c !== null ? `${Math.round(day.temp_max_c)}°` : "--";
    const minTemp = day.temp_min_c !== null ? `${Math.round(day.temp_min_c)}°` : "--";
    const rain = day.precipitation_mm > 0
      ? `💧 ${day.precipitation_mm.toFixed(1)}mm`
      : (day.precip_probability_pct > 0 ? `💧 ${day.precip_probability_pct}%` : `💧 0mm`);
    const wind = day.wind_speed_max_kmh !== null
      ? `💨 ${Math.round(day.wind_speed_max_kmh)} km/h`
      : "";

    return `
      <div class="weather-day-card ${isToday ? 'today' : ''}">
        <div class="weather-day-name">${escapeHtml(dayName)}</div>
        <div class="weather-day-date">${escapeHtml(dateFormatted)}</div>
        <div class="weather-day-icon">${escapeHtml(day.icon || "🌡️")}</div>
        <div class="weather-day-condition" title="${escapeHtml(day.condition || '')}">${escapeHtml(day.condition || "Fair")}</div>
        <div class="weather-day-temps">
          <span class="weather-temp-max">${escapeHtml(maxTemp)}</span>
          <span class="weather-temp-min">${escapeHtml(minTemp)}</span>
        </div>
        <div class="weather-day-meta">
          <div class="weather-meta-row weather-meta-rain">${escapeHtml(rain)}</div>
          ${wind ? `<div class="weather-meta-row weather-meta-wind">${escapeHtml(wind)}</div>` : ''}
        </div>
      </div>
    `;
  }).join("");

  container.innerHTML = html;
}

function handleCopyReport() {
  if (!dashboardData || !currentWeekKey) return;
  const week = dashboardData.weeks[currentWeekKey];
  const garmin = (dashboardData.garmin || {})[currentWeekKey] || null;
  const mDate = new Date(week.week_monday + "T00:00:00");
  const sDate = new Date(week.week_sunday + "T00:00:00");

  const sleepStr = garmin && garmin.total_sleep_h ? `${garmin.total_sleep_h.toFixed(1)}h` : "--";
  const rhrStr = garmin && garmin.avg_rhr ? `${garmin.avg_rhr}` : "--";
  const hrvStr = garmin && garmin.avg_hrv ? `${garmin.avg_hrv}` : "--";
  const acwr = week.acwr || {};

  const reportText = 
`⚡ Weekly Training Report (${mDate.toLocaleDateString("en-US", { month: "short", day: "numeric" })} – ${sDate.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })})
──────────────────────────────
⏱️ Total Time: ${formatDuration(week.total_time_sec)} (${week.activities.length} workouts)
🔥 Relative Effort: ${Math.round(week.total_relative_effort || 0)} (ACWR: ${acwr.acwr_ratio || "—"})

🏃 Running: ${week.run_dist_km.toFixed(2)} km / ${formatDuration(week.run_time_sec)}
🚴 Cycling: ${week.bike_dist_km.toFixed(2)} km / ${formatDuration(week.bike_time_sec)}${week.total_elevation_m > 0 ? ` / +${Math.round(week.total_elevation_m)}m` : ""}
🏊 Swimming: ${Math.round(week.swim_dist_m)} m / ${formatDuration(week.swim_time_sec)}
🏋️ Strength: ${formatDuration(week.strength_time_sec)}

😴 Garmin Health: Sleep ${sleepStr} • HRrest ${rhrStr} • HRV ${hrvStr}
──────────────────────────────
🤖 AI Coach Status: Readiness ${document.getElementById("readinessScore").textContent}`;

  navigator.clipboard.writeText(reportText).then(() => {
    showToast("📋 Report copied to clipboard (ready for WhatsApp/Coach)!", "success");
  }).catch(err => {
    showToast("Copy error: " + err.message, "error");
  });
}

async function handleSendWhatsApp() {
  const btn = document.getElementById("sendWhatsAppBtn");
  const origText = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = "<span>⏳ Sending...</span>";

  try {
    const res = await fetch("/api/notifications/whatsapp/next-day", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dry_run: false }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Dispatch failed");

    if (data.success) {
      showToast(`📱 Next-day brief sent to WhatsApp! (${data.target_date})`, "success");
    } else {
      showToast(`⚠️ WhatsApp: ${data.dispatch ? data.dispatch.detail : 'Dispatched with dry-run'}`, "info");
    }
  } catch (err) {
    showToast("WhatsApp dispatch error: " + err.message, "error");
  } finally {
    btn.disabled = false;
    btn.innerHTML = origText;
  }
}

async function handleGenerateAiFeedback() {
  if (!dashboardData || !currentWeekKey) return;

  const btn = document.getElementById("generateAiBtn");
  const outputBox = document.getElementById("aiOutputBox");
  btn.disabled = true;
  btn.innerHTML = "<span>⏳ Analyzing...</span>";
  outputBox.textContent = "Analyzing training volume, heart rate zones, and Garmin biometrics...";

  try {
    const week = dashboardData.weeks[currentWeekKey];
    const garmin = (dashboardData.garmin || {})[currentWeekKey] || null;
    const athleteNotes = document.getElementById("athleteNotesText").value;
    const fatigue = document.getElementById("fatigueSlider").value;
    const soreness = document.getElementById("sorenessSlider").value;
    const mood = document.getElementById("moodSlider").value;

    const payload = {
      week_summary: {
        run_dist: week.run_dist_km,
        bike_dist: week.bike_dist_km,
        swim_dist: week.swim_dist_m,
        total_time_seconds: week.total_time_sec,
        activities_count: week.activities.length,
        relative_effort: week.total_relative_effort,
        elevation_m: week.total_elevation_m,
      },
      garmin_health: garmin,
      athlete_notes: `${athleteNotes} [Fatigue: ${fatigue}/10, Soreness: ${soreness}/10, Mood: ${mood}/10]`,
    };

    const res = await fetch("/api/ai/coach", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!res.ok) throw new Error("AI Coaching API error");
    const data = await res.json();

    let fullText = data.feedback + "\n\n📌 Coach Recommendations:\n";
    (data.recommendations || []).forEach((r, idx) => {
      fullText += `${idx + 1}. ${r}\n`;
    });
    outputBox.textContent = fullText;

    if (data.readiness_score) {
      document.getElementById("readinessScore").textContent = `${data.readiness_score}%`;
      document.getElementById("readinessCircle").style.setProperty("--score", data.readiness_score);
    }
    if (data.source) {
      document.getElementById("aiSourceBadge").textContent = data.source.includes("gemini") ? data.source : "AI Heuristics";
    }

    showToast("✨ AI Coaching analysis generated successfully!", "success");
  } catch (err) {
    console.error("AI feedback error:", err);
    outputBox.textContent = "Error during analysis: " + err.message;
    showToast("AI analysis error: " + err.message, "error");
  } finally {
    btn.disabled = false;
    btn.innerHTML = "<span>✨ Generate AI Feedback</span>";
  }
}

async function handleSheetSync() {
  const btn = document.getElementById("syncSheetBtn");
  btn.disabled = true;
  btn.innerHTML = "<span>🔄 Syncing...</span>";

  try {
    const res = await fetch("/api/sheet/sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ count: 35 }),
    });

    if (!res.ok) throw new Error("Google Sheets Sync failed");
    const data = await res.json();

    showToast(`✅ Successfully synced ${data.synced_activities} activities to Google Sheet!`, "success");
    await initApp();
  } catch (err) {
    console.error("Sync error:", err);
    showToast("Google Sheets sync error: " + err.message, "error");
  } finally {
    btn.disabled = false;
    btn.innerHTML = "<span>📊 Sync to Google Sheet</span>";
  }
}

function showToast(message, type = "success") {
  const container = document.getElementById("toastContainer");
  const toast = document.createElement("div");
  toast.className = `toast ${type}`;
  toast.innerHTML = `<span>${type === "success" ? "✅" : "⚠️"}</span><span>${message}</span>`;

  container.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = "0";
    toast.style.transform = "translateX(100%)";
    setTimeout(() => toast.remove(), 300);
  }, 4000);
}

// ── Run Durability Panel ─────────────────────────────────────────────────────

async function fetchDurability() {
  // Loaded separately from the dashboard: it is a different endpoint answering a
  // different question, and a failure here must not blank the rest of the page.
  try {
    const res = await fetch("/api/durability");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    renderDurability(await res.json());
  } catch (err) {
    console.error("Durability load failed:", err);
    const badge = document.getElementById("durabilityRiskBadge");
    if (badge) {
      badge.textContent = "Unavailable";
      applyBadgeStyle(badge, RISK_STYLES.unknown);
    }
  }
}

function applyBadgeStyle(el, style) {
  el.style.color = style.color;
  el.style.borderColor = style.border;
  // Same hue at low alpha for the fill, so one colour definition drives both.
  el.style.background = style.border.replace(/[\d.]+\)$/, "0.12)");
}

function formatSignal(signal) {
  const v = signal.value;
  if (v === null || v === undefined) return { value: "—", threshold: "" };

  switch (signal.key) {
    case "run_ramp_rate":
      return { value: `${v > 0 ? "+" : ""}${v.toFixed(1)}%`, threshold: `safe ≤ +${signal.threshold}%` };
    case "run_rest_days":
      return { value: `${v} ${v === 1 ? "day" : "days"}`, threshold: `need ≥ ${signal.threshold}` };
    case "long_run_share":
      return { value: `${Math.round(v * 100)}%`, threshold: `max ${Math.round(signal.threshold * 100)}%` };
    case "run_acwr":
      return { value: v.toFixed(2), threshold: signal.zone || `max ${signal.threshold}` };
    default:
      return { value: `${v}`, threshold: `max ${signal.threshold}` };
  }
}

function renderDurability(data) {
  const durability = data.durability || {};
  const badge = document.getElementById("durabilityRiskBadge");
  const risk = RISK_STYLES[durability.risk_level] || RISK_STYLES.unknown;
  badge.textContent = risk.label;
  applyBadgeStyle(badge, risk);

  const list = document.getElementById("durabilitySignals");
  list.innerHTML = "";
  (durability.signals || []).forEach((signal) => {
    const { value, threshold } = formatSignal(signal);
    const style = SEVERITY_STYLES[signal.severity] || SEVERITY_STYLES.unknown;
    const row = document.createElement("div");
    row.className = "durability-signal";
    row.style.borderLeftColor = style.color;
    row.innerHTML = `
      <span class="durability-signal-label">${SIGNAL_LABELS[signal.key] || signal.key}</span>
      <span>
        <span class="durability-signal-value" style="color: ${style.color}">${value}</span>
        <span class="durability-signal-threshold">${threshold}</span>
      </span>
    `;
    list.appendChild(row);
  });

  renderRampHistory(durability.ramp_history);
  renderCrossTraining(data.cross_training);
}

// The signals above score only the latest week, which early in a week is mostly
// empty and reads as safe. The ramp strip keeps the recent swings on screen.
function renderRampHistory(history) {
  const host = document.getElementById("durabilityRamp");
  host.innerHTML = "";
  if (!history || history.length < 2) return;

  history.slice(-8).forEach((week) => {
    const style = SEVERITY_STYLES[week.severity] || SEVERITY_STYLES.unknown;
    const pct = week.change_pct === null ? "—" : `${week.change_pct > 0 ? "+" : ""}${Math.round(week.change_pct)}%`;
    const date = new Date(week.week_key + "T00:00:00");
    const cell = document.createElement("div");
    cell.className = "durability-ramp-week";
    cell.style.borderBottomColor = style.color;
    cell.title = `${week.run_km} km (was ${week.prev_run_km === null ? "n/a" : week.prev_run_km + " km"})`;
    cell.innerHTML = `
      <div class="durability-ramp-week-date">${date.toLocaleDateString("en-US", { month: "short", day: "numeric" })}</div>
      <div class="durability-ramp-week-pct" style="color: ${style.color}">${pct}</div>
    `;
    host.appendChild(cell);
  });
}

function renderCrossTraining(plan) {
  const box = document.getElementById("durabilityPlan");
  box.innerHTML = "";
  if (!plan) return;

  const swaps = (plan.substitutions || [])
    .map((sub) => {
      const minutes = sub.equivalent_minutes ? `≈ ${sub.equivalent_minutes} min` : "no history yet";
      return `
        <div class="durability-swap-box">
          <div class="durability-swap-label">${MODALITY_LABELS[sub.modality] || sub.modality}</div>
          <div class="durability-swap-value">${sub.replacement_load}</div>
          <div class="durability-swap-sub">${minutes}</div>
        </div>`;
    })
    .join("");

  const strength = plan.strength || {};
  const chips = (strength.exercises || [])
    .map((ex) => `<span class="durability-exercise-chip">${EXERCISE_LABELS[ex] || ex}</span>`)
    .join("");

  box.innerHTML = `
    <div class="durability-plan-title">This week's load split</div>
    <div class="durability-swap-grid">
      <div class="durability-swap-box">
        <div class="durability-swap-label">🏃 Run</div>
        <div class="durability-swap-value">${plan.safe_run_load}</div>
        <div class="durability-swap-sub">of ${plan.target_run_load} effort</div>
      </div>
      ${swaps}
    </div>
    <div class="durability-plan-note">
      ${plan.shortfall_load > 0
        ? `${plan.shortfall_load} effort moved off the pavement to keep the aerobic dose without the impact.`
        : "Running is holding up — the full target can stay on the pavement."}
    </div>
    <div class="durability-plan-title" style="margin-top: 12px;">
      Single-leg strength · ${strength.sessions_per_week || 0}× / week
    </div>
    <div class="durability-exercises">${chips}</div>
  `;
}

// ── AI Coach Chat ────────────────────────────────────────────────────────────

// Mirrored to localStorage so a page refresh resumes the same conversation
// instead of silently starting a new one the server will never link up.
let chatSessionId = localStorage.getItem("coachSessionId") || null;
let chatBusy = false;

const CHAT_TOOL_LABELS = {
  get_week_summary: "reading your week",
  get_activities: "pulling your activities",
  get_training_load: "checking your load",
  get_run_durability: "assessing run durability",
  get_race_projections: "running projections",
  get_health_metrics: "checking recovery data",
  search_web: "searching the web",
  find_exercise_videos: "finding videos",
  remember_fact: "noting that down",
};

const CHAT_GREETING =
  "Ask me about your training load, a race you're eyeing, gear, or a niggle. " +
  "I can see your activities, your recovery data and your projections — and I'll " +
  "remember what you tell me about yourself.";

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text === null || text === undefined ? "" : String(text);
  return div.innerHTML;
}

function initChatDrawer() {
  const messages = document.getElementById("chatMessages");
  if (messages && !messages.children.length) {
    messages.innerHTML = `<div class="chat-empty">${CHAT_GREETING}</div>`;
  }
}

function toggleChatDrawer(open) {
  const drawer = document.getElementById("chatDrawer");
  const launcher = document.getElementById("chatLauncher");
  const shouldOpen = open === undefined ? !drawer.classList.contains("open") : open;

  drawer.classList.toggle("open", shouldOpen);
  drawer.setAttribute("aria-hidden", shouldOpen ? "false" : "true");
  launcher.classList.toggle("hidden", shouldOpen);

  if (shouldOpen) document.getElementById("chatInput").focus();
}

function appendChatMessage(role, text, extras = {}) {
  const messages = document.getElementById("chatMessages");
  const empty = messages.querySelector(".chat-empty");
  if (empty) empty.remove();

  const bubble = document.createElement("div");
  bubble.className = `chat-msg ${role}`;
  bubble.innerHTML = escapeHtml(text);

  // Citations are rendered as real links: a grounded answer the athlete cannot
  // follow up on is barely better than an ungrounded one.
  const sources = (extras.sources || []).slice(0, 4);
  if (sources.length) {
    const block = document.createElement("div");
    block.className = "chat-msg-sources";
    block.innerHTML = sources
      .map(
        (s) =>
          `<a href="${escapeHtml(s.uri)}" target="_blank" rel="noopener noreferrer">🔗 ${escapeHtml(
            s.title || s.uri,
          )}</a>`,
      )
      .join("");
    bubble.appendChild(block);
  }

  messages.appendChild(bubble);
  messages.scrollTop = messages.scrollHeight;
  return bubble;
}

function renderToolActivity(tools) {
  const box = document.getElementById("chatToolActivity");
  if (!tools || !tools.length) {
    box.hidden = true;
    box.textContent = "";
    return;
  }
  const labels = tools.map((t) => CHAT_TOOL_LABELS[t] || t.replace(/_/g, " "));
  box.hidden = false;
  box.textContent = `⚙️ ${labels.join(" · ")}`;
}

async function sendChatMessage() {
  const input = document.getElementById("chatInput");
  const sendBtn = document.getElementById("chatSendBtn");
  const badge = document.getElementById("chatModelBadge");
  const message = input.value.trim();
  if (!message || chatBusy) return;

  chatBusy = true;
  input.value = "";
  input.style.height = "auto";
  sendBtn.disabled = true;
  appendChatMessage("user", message);

  const thinking = appendChatMessage("coach", "…");
  renderToolActivity(null);
  badge.textContent = "thinking…";

  try {
    const res = await fetch("/api/ai/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message,
        session_id: chatSessionId,
        // The week the athlete has open, so "how was this week?" resolves
        // without them naming a date.
        week_context: currentWeekKey && dashboardData ? dashboardData.weeks[currentWeekKey] : null,
      }),
    });

    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);

    if (data.session_id && data.session_id !== chatSessionId) {
      chatSessionId = data.session_id;
      localStorage.setItem("coachSessionId", chatSessionId);
    }

    thinking.remove();
    appendChatMessage("coach", data.reply || "(no reply)", { sources: data.sources });
    renderToolActivity(data.tools_used);
    badge.textContent = data.source_model || "ready";
  } catch (err) {
    console.error("Chat error:", err);
    thinking.remove();
    appendChatMessage("error", `Couldn't reach the coach: ${err.message}`);
    badge.textContent = "unavailable";
  } finally {
    chatBusy = false;
    sendBtn.disabled = false;
    input.focus();
  }
}

async function resetChatSession() {
  const closing = chatSessionId;
  chatSessionId = null;
  localStorage.removeItem("coachSessionId");

  document.getElementById("chatMessages").innerHTML = `<div class="chat-empty">${CHAT_GREETING}</div>`;
  renderToolActivity(null);
  document.getElementById("chatModelBadge").textContent = "ready";

  // The closing conversation is the last chance to keep what was mentioned in
  // passing. Fire-and-forget: the new chat must not wait on an extraction pass,
  // and a failed one costs a fact, not the athlete's next question.
  if (!closing) return;
  try {
    const res = await fetch("/api/coach/memory/extract", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: closing }),
    });
    if (!res.ok) return;
    const data = await res.json();
    const kept = (data.stored || []).length;
    if (kept) showToast(`Filed ${kept} thing${kept === 1 ? "" : "s"} to remember about you`);
  } catch (err) {
    console.error("Fact extraction failed:", err);
  }
}

async function toggleChatMemoryPanel() {
  const panel = document.getElementById("chatMemoryPanel");
  if (!panel.hidden) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  await loadChatMemory();
}

async function loadChatMemory() {
  const list = document.getElementById("chatMemoryList");
  list.innerHTML = `<div class="chat-memory-item">Loading…</div>`;
  try {
    const res = await fetch("/api/coach/memory");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    renderChatMemory(data.facts || []);
  } catch (err) {
    console.error("Memory load failed:", err);
    list.innerHTML = `<div class="chat-memory-item">Couldn't load memory.</div>`;
  }
}

function renderChatMemory(facts) {
  const list = document.getElementById("chatMemoryList");
  if (!facts.length) {
    list.innerHTML = `<div class="chat-memory-item">Nothing stored yet. Tell me something about yourself.</div>`;
    return;
  }

  list.innerHTML = facts
    .map(
      (f) => `
    <div class="chat-memory-item">
      <span>${escapeHtml(f.fact)}
        <span class="chat-memory-category">${escapeHtml(f.category || "other")}</span>
      </span>
      <button class="chat-memory-forget" data-fact-id="${escapeHtml(f.id)}" title="Forget this">✕</button>
    </div>`,
    )
    .join("");

  list.querySelectorAll(".chat-memory-forget").forEach((btn) => {
    btn.addEventListener("click", () => forgetChatFact(btn.dataset.factId));
  });
}

async function forgetChatFact(factId) {
  try {
    const res = await fetch(`/api/coach/memory/${encodeURIComponent(factId)}`, { method: "DELETE" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    showToast("Forgotten");
    await loadChatMemory();
  } catch (err) {
    console.error("Forget failed:", err);
    showToast("Couldn't forget that: " + err.message, "error");
  }
}
