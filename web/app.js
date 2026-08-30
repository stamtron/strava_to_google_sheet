// Endurance AI Dashboard — Frontend JavaScript State & Logic

let dashboardData = null;
let currentWeekKey = null;
let disciplineChart = null;
let recoveryChart = null;

const DAY_NAMES_EL = ["Δευτέρα", "Τρίτη", "Τετάρτη", "Πέμπτη", "Παρασκευή", "Σάββατο", "Κυριακή"];
const DAY_SHORT_EL = ["ΔΕΥ", "ΤΡΙ", "ΤΕΤ", "ΠΕΜ", "ΠΑΡ", "ΣΑΒ", "ΚΥΡ"];

// Document Ready
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

    const weekKeys = Object.keys(dashboardData.weeks || {}).sort().reverse();
    if (weekKeys.length > 0) {
      selectWeek(weekKeys[0]);
    }

    renderPredictions(dashboardData.predictions);
  } catch (err) {
    console.error("Initialization error:", err);
    showToast("Σφάλμα φόρτωσης δεδομένων: " + err.message, "error");
  }
}

function setupEventListeners() {
  document.getElementById("weekSelector").addEventListener("change", (e) => {
    selectWeek(e.target.value);
  });

  document.getElementById("syncSheetBtn").addEventListener("click", handleSheetSync);
  document.getElementById("generateAiBtn").addEventListener("click", handleGenerateAiFeedback);

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
    const mDate = new Date(w.week_monday);
    const sDate = new Date(w.week_sunday);
    const opt = document.createElement("option");
    opt.value = key;
    opt.textContent = `${mDate.toLocaleDateString("el-GR", { day: "numeric", month: "short" })} – ${sDate.toLocaleDateString("el-GR", { day: "numeric", month: "short", year: "numeric" })} ${idx === 0 ? "(Τρέχουσα)" : ""}`;
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
  if (!seconds || seconds <= 0) return "0λ";
  const hrs = Math.floor(seconds / 3600);
  const mins = Math.floor((seconds % 3600) / 60);
  if (hrs > 0) return `${hrs}ω ${mins}λ`;
  return `${mins}λ`;
}

function renderMetricCards(week, garmin) {
  document.getElementById("metricTotalHours").textContent = formatDuration(week.total_time_sec);
  document.getElementById("metricActivitiesCount").textContent = `${week.activities.length} προπονήσεις αυτή την εβδομάδα`;

  document.getElementById("metricRunDist").textContent = `${week.run_dist_km.toFixed(2)} χλμ`;
  document.getElementById("metricRunTime").textContent = formatDuration(week.run_time_sec);

  document.getElementById("metricBikeDist").textContent = `${week.bike_dist_km.toFixed(2)} χλμ`;
  document.getElementById("metricBikeTime").textContent = formatDuration(week.bike_time_sec);

  document.getElementById("metricSwimDist").textContent = `${Math.round(week.swim_dist_m)} μ`;
  document.getElementById("metricSwimTime").textContent = formatDuration(week.swim_time_sec);

  if (garmin && garmin.total_sleep_h) {
    document.getElementById("metricSleep").textContent = `${garmin.total_sleep_h.toFixed(1)}h Ύπνος`;
    document.getElementById("metricRhr").textContent = garmin.avg_rhr ? `${garmin.avg_rhr} bpm` : "--";
    document.getElementById("metricHrv").textContent = garmin.avg_hrv ? `${garmin.avg_hrv} ms` : "--";
  } else {
    document.getElementById("metricSleep").textContent = "--h Ύπνος";
    document.getElementById("metricRhr").textContent = "--";
    document.getElementById("metricHrv").textContent = "--";
  }
}

function formatPace(sport, movingTime, distanceMeters) {
  if (!movingTime || movingTime <= 0 || !distanceMeters || distanceMeters <= 0) return "";
  if (sport === "Swim") {
    // Swimming pace per 100m (with halved distance)
    const adjDistM = distanceMeters / 2.0;
    const paceSec = movingTime / (adjDistM / 100.0);
    const m = Math.floor(paceSec / 60);
    const s = Math.floor(paceSec % 60);
    return `${m}:${s.toString().padStart(2, "0")} /100m`;
  } else if (sport.includes("Ride")) {
    // Speed km/h
    const speedKmh = (distanceMeters / 1000.0) / (movingTime / 3600.0);
    return `${speedKmh.toFixed(1)} km/h`;
  } else {
    // Running pace /km
    const paceSec = movingTime / (distanceMeters / 1000.0);
    const m = Math.floor(paceSec / 60);
    const s = Math.floor(paceSec % 60);
    return `${m}:${s.toString().padStart(2, "0")} /km`;
  }
}

function renderCalendar(week) {
  const grid = document.getElementById("daysGrid");
  grid.innerHTML = "";

  const mDateStr = week.week_monday; // e.g. "2026-08-24"
  const mDate = new Date(mDateStr + "T00:00:00");
  const sDate = new Date(week.week_sunday + "T00:00:00");
  const dateRangeEl = document.getElementById("calendarDateRange");
  dateRangeEl.textContent = `${mDate.toLocaleDateString("el-GR", { day: "numeric", month: "long" })} – ${sDate.toLocaleDateString("el-GR", { day: "numeric", month: "long", year: "numeric" })}`;

  // Bucket activities by weekday (0 = Monday, 6 = Sunday) using exact calendar day calculation
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
    header.innerHTML = `<div>${DAY_SHORT_EL[i]}</div><div style="font-size: 11px; color: var(--text-muted); font-weight: normal;">${dayDate.getDate()}/${dayDate.getMonth() + 1}</div>`;
    col.appendChild(header);

    const acts = daysActivities[i];
    if (acts.length === 0) {
      const empty = document.createElement("div");
      empty.style.cssText = "color: var(--text-muted); font-size: 11px; text-align: center; margin-top: 20px;";
      empty.textContent = "Ξεκούραση / Rest";
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
          // Run / Hike / Walk
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
  // 1. Discipline Breakdown (Doughnut)
  const ctxDisc = document.getElementById("disciplineChart").getContext("2d");
  disciplineChart = new Chart(ctxDisc, {
    type: "doughnut",
    data: {
      labels: ["Τρέξιμο", "Ποδηλασία", "Κολύμβηση", "Ενδυνάμωση"],
      datasets: [{
        data: [1, 1, 1, 1],
        backgroundColor: ["#f97316", "#00f2fe", "#38f9d7", "#b224ef"],
        borderWidth: 0,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          position: "bottom",
          labels: { color: "#94a3b8", font: { family: "Inter", size: 12 } },
        },
      },
      cutout: "70%",
    },
  });

  // 2. Recovery & Health (Bar / Line)
  const ctxRec = document.getElementById("recoveryChart").getContext("2d");
  recoveryChart = new Chart(ctxRec, {
    type: "bar",
    data: {
      labels: ["Ύπνος (h)", "HRV (ms)", "HRrest (bpm)"],
      datasets: [{
        label: "Garmin Health",
        data: [50, 65, 48],
        backgroundColor: ["#7928ca", "#00f2fe", "#ff0080"],
        borderRadius: 8,
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

function renderPredictions(predData) {
  if (!predData || !predData.predictions) return;

  const tbody = document.getElementById("predictionsBody");
  tbody.innerHTML = "";

  document.getElementById("basePaceBadge").textContent = `Βάση: ${predData.base_pace_used}`;

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

async function handleGenerateAiFeedback() {
  if (!dashboardData || !currentWeekKey) return;

  const btn = document.getElementById("generateAiBtn");
  const outputBox = document.getElementById("aiOutputBox");
  btn.disabled = true;
  btn.innerHTML = "<span>⏳ Ανάλυση σε εξέλιξη...</span>";
  outputBox.textContent = "Το AI αναλύει τον προπονητικό όγκο, τις ζώνες καρδιακών παλμών και τα βιομετρικά Garmin...";

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
      },
      garmin_health: garmin,
      athlete_notes: `${athleteNotes} [Κόπωση: ${fatigue}/10, Ενόχληση: ${soreness}/10, Διάθεση: ${mood}/10]`,
    };

    const res = await fetch("/api/ai/coach", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!res.ok) throw new Error("AI Coaching API error");
    const data = await res.json();

    // Update UI
    let fullText = data.feedback + "\n\n📌 Συμβουλές Προπονητή:\n";
    (data.recommendations || []).forEach((r, idx) => {
      fullText += `${idx + 1}. ${r}\n`;
    });
    outputBox.textContent = fullText;

    if (data.readiness_score) {
      document.getElementById("readinessScore").textContent = `${data.readiness_score}%`;
      document.getElementById("readinessCircle").style.setProperty("--score", data.readiness_score);
    }
    if (data.source) {
      document.getElementById("aiSourceBadge").textContent = data.source === "gemini-2.5-flash" ? "Gemini 2.5 Flash" : "AI Heuristics";
    }

    showToast("✨ Η ανάλυση προπόνησης ολοκληρώθηκε επιτυχώς!", "success");
  } catch (err) {
    console.error("AI feedback error:", err);
    outputBox.textContent = "Σφάλμα κατά την ανάλυση: " + err.message;
    showToast("Σφάλμα ανάλυσης AI: " + err.message, "error");
  } finally {
    btn.disabled = false;
    btn.innerHTML = "<span>✨ Δημιουργία AI Ανατροφοδότησης</span>";
  }
}

async function handleSheetSync() {
  const btn = document.getElementById("syncSheetBtn");
  btn.disabled = true;
  btn.innerHTML = "<span>🔄 Συγχρονισμός...</span>";

  try {
    const res = await fetch("/api/sheet/sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ count: 35 }),
    });

    if (!res.ok) throw new Error("Google Sheets Sync failed");
    const data = await res.json();

    showToast(`✅ Επιτυχής συγχρονισμός ${data.synced_activities} προπονήσεων στο Google Sheet!`, "success");
    // Reload dashboard
    await initApp();
  } catch (err) {
    console.error("Sync error:", err);
    showToast("Σφάλμα συγχρονισμού Google Sheets: " + err.message, "error");
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
