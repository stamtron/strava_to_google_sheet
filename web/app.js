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

let currentTriMode = "pb"; // "pb" or "train"
document.addEventListener("DOMContentLoaded", () => {
  initApp();
  setupEventListeners();
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
    renderTriathlonPredictions(dashboardData.triathlon);
    updateProgressionCharts(dashboardData.progression);
  } catch (err) {
    console.error("Initialization error:", err);
    showToast("Failed to load dashboard data: " + err.message, "error");
  }
}

function setupEventListeners() {
  document.getElementById("weekSelector").addEventListener("change", (e) => {
    selectWeek(e.target.value);
  });

  document.getElementById("syncSheetBtn").addEventListener("click", handleSheetSync);
  document.getElementById("generateAiBtn").addEventListener("click", handleGenerateAiFeedback);
  document.getElementById("copyReportBtn").addEventListener("click", handleCopyReport);

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
  const tabProgression = document.getElementById("tabProgressionBtn");
  const weeklyView = document.getElementById("weeklyView");
  const progressionView = document.getElementById("progressionView");

  tabWeekly.addEventListener("click", () => {
    tabWeekly.classList.add("active");
    tabProgression.classList.remove("active");
    weeklyView.style.display = "grid";
    progressionView.classList.remove("active");
  });

  tabProgression.addEventListener("click", () => {
    tabProgression.classList.add("active");
    tabWeekly.classList.remove("active");
    weeklyView.style.display = "none";
    progressionView.classList.add("active");
    // Trigger chart resize
    if (effortProgressionChart) effortProgressionChart.resize();
    if (volumeProgressionChart) volumeProgressionChart.resize();
    if (elevationProgressionChart) elevationProgressionChart.resize();
    if (acwrProgressionChart) acwrProgressionChart.resize();
  });

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

  if (garmin && garmin.total_sleep_h) {
    document.getElementById("metricSleep").textContent = `${garmin.total_sleep_h.toFixed(1)}h Sleep`;
    document.getElementById("metricRhr").textContent = garmin.avg_rhr ? `${garmin.avg_rhr} bpm` : "--";
    document.getElementById("metricHrv").textContent = garmin.avg_hrv ? `${garmin.avg_hrv} ms` : "--";
  } else {
    document.getElementById("metricSleep").textContent = "--h Sleep";
    document.getElementById("metricRhr").textContent = "--";
    document.getElementById("metricHrv").textContent = "--";
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
  const mDate = new Date(mDateStr + "T00:00:00");
  const sDate = new Date(week.week_sunday + "T00:00:00");
  const dateRangeEl = document.getElementById("calendarDateRange");
  dateRangeEl.textContent = `${mDate.toLocaleDateString("en-US", { month: "long", day: "numeric" })} – ${sDate.toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric" })}`;

  const daysActivities = [[], [], [], [], [], [], []];
  week.activities.forEach((act) => {
    const rawDt = act.start_date_local || "";
    const actDateStr = rawDt.substring(0, 10);
    const actDateObj = new Date(actDateStr + "T00:00:00");
    const diffTime = actDateObj.getTime() - mDate.getTime();
    const dayIdx = Math.round(diffTime / (1000 * 3600 * 24));
    if (dayIdx >= 0 && dayIdx <= 6) {
      daysActivities[dayIdx].push(act);
    }
  });

  for (let i = 0; i < 7; i++) {
    const dayDate = new Date(mDate);
    dayDate.setDate(dayDate.getDate() + i);

    const col = document.createElement("div");
    col.className = "day-column";

    const header = document.createElement("div");
    header.className = "day-header";
    header.innerHTML = `<div>${DAY_SHORT_EN[i]}</div><div style="font-size: 11px; color: var(--text-muted); font-weight: normal;">${dayDate.getMonth() + 1}/${dayDate.getDate()}</div>`;
    col.appendChild(header);

    const acts = daysActivities[i];
    if (acts.length === 0) {
      const empty = document.createElement("div");
      empty.style.cssText = "color: var(--text-muted); font-size: 11px; text-align: center; margin-top: 20px;";
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
          <div class="act-type ${sportClass}">${icon} ${a.name || sport}</div>
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
