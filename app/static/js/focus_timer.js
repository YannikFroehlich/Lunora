(function () {
  // ---- Pure helpers (covered by frontend/focus_timer.test.js) -----------------------------------

  const PHASE_LABELS = { focus: "Fokus", short: "Kurze Pause", long: "Lange Pause" };
  const DEFAULT_SETTINGS = { focus: 25, short: 5, long: 15, longEvery: 4 };
  const SETTING_LIMITS = { focus: [1, 180], short: [1, 60], long: [1, 60], longEvery: [2, 12] };

  function formatClock(totalSeconds) {
    const seconds = Math.max(0, Math.ceil(totalSeconds));
    const minutes = Math.floor(seconds / 60);
    return `${String(minutes).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
  }

  // Every longEvery-th finished focus block earns a long break; any break leads back to focus.
  function nextPhase(phase, completedFocus, longEvery) {
    if (phase !== "focus") return { phase: "focus", completedFocus };
    const completed = completedFocus + 1;
    return { phase: completed % longEvery === 0 ? "long" : "short", completedFocus: completed };
  }

  function cleanSettings(raw) {
    const settings = {};
    Object.entries(DEFAULT_SETTINGS).forEach(([key, fallback]) => {
      const [min, max] = SETTING_LIMITS[key];
      const value = Math.round(Number(raw?.[key]));
      settings[key] = Number.isFinite(value) && value >= min && value <= max ? value : fallback;
    });
    return settings;
  }

  const HISTORY_DAYS = 60;

  // Local calendar day, so a unit finished at 23:30 counts for that evening, not UTC's next day.
  function dayKey(date) {
    const month = String(date.getMonth() + 1).padStart(2, "0");
    return `${date.getFullYear()}-${month}-${String(date.getDate()).padStart(2, "0")}`;
  }

  function daysBefore(date, count) {
    return new Date(date.getFullYear(), date.getMonth(), date.getDate() - count);
  }

  // Days in a row with at least one finished unit. Like the habits, a still-empty today doesn't
  // break the streak; it just doesn't count yet.
  function focusStreak(history, today) {
    let streak = 0;
    for (let offset = history[dayKey(today)] ? 0 : 1; history[dayKey(daysBefore(today, offset))] > 0; offset += 1) {
      streak += 1;
    }
    return streak;
  }

  // Units of the last seven days, today included.
  function weekTotal(history, today) {
    let total = 0;
    for (let offset = 0; offset < 7; offset += 1) total += Number(history[dayKey(daysBefore(today, offset))]) || 0;
    return total;
  }

  // Keeps only plausible counts of the last HISTORY_DAYS days.
  function cleanHistory(raw, today) {
    const oldest = dayKey(daysBefore(today, HISTORY_DAYS - 1));
    const history = {};
    Object.entries(raw && typeof raw === "object" ? raw : {}).forEach(([day, count]) => {
      const value = Math.round(Number(count));
      if (/^\d{4}-\d{2}-\d{2}$/.test(day) && day >= oldest && value > 0 && value < 1000) history[day] = value;
    });
    return history;
  }

  globalThis.LunoraFocusTimer = {
    formatClock,
    nextPhase,
    cleanSettings,
    dayKey,
    focusStreak,
    weekTotal,
    cleanHistory,
    DEFAULT_SETTINGS,
  };

  if (typeof document === "undefined") return;
  const root = document.querySelector("[data-focus-timer]");
  if (!root) return;

  // ---- Timer ------------------------------------------------------------------------------------

  const STORAGE_KEY = "lunora.focusTimer";
  const clock = root.querySelector("[data-focus-clock]");
  const phaseLabel = root.querySelector("[data-focus-phase-label]");
  const progress = root.querySelector("[data-focus-progress]");
  const toggleButton = root.querySelector("[data-focus-toggle]");
  const countLabel = root.querySelector("[data-focus-count]");
  const live = root.querySelector("[data-focus-live]");
  const settingsForm = root.querySelector("[data-focus-settings]");
  const taskSelect = root.querySelector("[data-focus-task]");
  const circumference = 2 * Math.PI * Number(progress.getAttribute("r"));
  const pageTitle = document.title;

  function loadStored() {
    try {
      return JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
    } catch (_error) {
      return {};
    }
  }

  const stored = loadStored();
  let settings = cleanSettings(stored.settings);
  const history = cleanHistory(stored.history, new Date());
  // Counts from before the history existed.
  if (!stored.history && stored.day === new Date().toDateString() && Number(stored.completedToday) > 0) {
    history[dayKey(new Date())] = Number(stored.completedToday);
  }
  let phase = "focus";
  let completedFocus = 0;
  let remaining = settings.focus * 60;
  let endsAt = null;
  let timerId = null;

  function store() {
    try {
      localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({ settings, history: cleanHistory(history, new Date()), taskId: taskSelect?.value || "" }),
      );
    } catch (_error) {
      // Private windows may block storage; the timer still works for this visit.
    }
  }

  Object.entries(settings).forEach(([key, value]) => {
    const input = settingsForm.elements.namedItem(key);
    if (input) input.value = value;
  });

  function phaseSeconds(name) {
    return settings[name] * 60;
  }

  function render() {
    const running = endsAt !== null;
    clock.textContent = formatClock(remaining);
    phaseLabel.textContent = PHASE_LABELS[phase];
    progress.style.strokeDasharray = `${circumference}`;
    progress.style.strokeDashoffset = `${circumference * (1 - remaining / phaseSeconds(phase))}`;
    root.querySelectorAll("[data-focus-phase-chip]").forEach((chip) => {
      chip.classList.toggle("is-active", chip.dataset.focusPhaseChip === phase);
    });
    toggleButton.querySelector("i").className = `fa-solid ${running ? "fa-pause" : "fa-play"}`;
    toggleButton.querySelector("span").textContent = running ? "Pausieren" : "Starten";
    document.title = running ? `${formatClock(remaining)} • ${PHASE_LABELS[phase]}` : pageTitle;
    const today = new Date();
    const completedToday = history[dayKey(today)] || 0;
    const streak = focusStreak(history, today);
    const parts = [`Heute ${completedToday} Fokus-Einheit${completedToday === 1 ? "" : "en"}`];
    parts.push(`${weekTotal(history, today)} in 7 Tagen`);
    if (streak > 1) parts.push(`${streak} Tage in Folge`);
    countLabel.textContent = parts.join(" · ");
  }

  function beep() {
    try {
      const audio = new AudioContext();
      const tone = audio.createOscillator();
      const gain = audio.createGain();
      tone.frequency.value = 660;
      gain.gain.setValueAtTime(0.15, audio.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, audio.currentTime + 0.8);
      tone.connect(gain).connect(audio.destination);
      tone.onended = () => audio.close();
      tone.start();
      tone.stop(audio.currentTime + 0.8);
    } catch (_error) {
      // Audio is optional.
    }
  }

  function announce(message) {
    live.textContent = message;
    if (document.hidden && "Notification" in window && Notification.permission === "granted") {
      new Notification("Lunora Fokus-Timer", { body: message, tag: "lunora-focus-timer" });
    }
  }

  // ---- Time tracking: a running focus block books its time on the chosen task -------------------

  let trackedTaskId = null;

  function trackingBody(action, taskId) {
    const body = new FormData();
    body.append("csrfmiddlewaretoken", root.querySelector("[name=csrfmiddlewaretoken]")?.value || "");
    body.append("action", action);
    body.append("task_id", taskId);
    return body;
  }

  function startTracking() {
    if (phase !== "focus" || !taskSelect?.value) return;
    trackedTaskId = taskSelect.value;
    fetch(root.dataset.trackUrl, {
      method: "POST",
      body: trackingBody("start", trackedTaskId),
      credentials: "same-origin",
    }).catch(() => {});
  }

  function stopTracking({ leaving = false } = {}) {
    if (trackedTaskId === null) return;
    const body = trackingBody("stop", trackedTaskId);
    trackedTaskId = null;
    // A closing tab may cancel a fetch; a beacon still arrives.
    if (leaving && navigator.sendBeacon) {
      navigator.sendBeacon(root.dataset.trackUrl, body);
    } else {
      fetch(root.dataset.trackUrl, { method: "POST", body, credentials: "same-origin" }).catch(() => {});
    }
  }

  function stop() {
    if (endsAt !== null) remaining = Math.max(0, (endsAt - Date.now()) / 1000);
    endsAt = null;
    clearInterval(timerId);
    timerId = null;
    stopTracking();
  }

  function advance({ finished }) {
    stop();
    if (finished && phase === "focus") {
      const today = dayKey(new Date());
      history[today] = (history[today] || 0) + 1;
      store();
    }
    ({ phase, completedFocus } = nextPhase(phase, completedFocus, settings.longEvery));
    remaining = phaseSeconds(phase);
    if (finished) {
      beep();
      announce(
        phase === "focus" ? "Pause vorbei, weiter geht es." : `Geschafft! Zeit für eine ${PHASE_LABELS[phase]}.`,
      );
    }
    render();
  }

  function tick() {
    remaining = Math.max(0, (endsAt - Date.now()) / 1000);
    if (remaining <= 0) {
      advance({ finished: true });
      return;
    }
    render();
  }

  function start() {
    // Measuring against an end timestamp keeps the timer exact while the tab is throttled.
    endsAt = Date.now() + remaining * 1000;
    timerId = setInterval(tick, 250);
    startTracking();
    if ("Notification" in window && Notification.permission === "default") {
      Notification.requestPermission().catch(() => {});
    }
    render();
  }

  toggleButton.addEventListener("click", () => {
    if (endsAt === null) {
      start();
    } else {
      stop();
      render();
    }
  });

  root.querySelector("[data-focus-reset]").addEventListener("click", () => {
    stop();
    remaining = phaseSeconds(phase);
    render();
  });

  root.querySelector("[data-focus-skip]").addEventListener("click", () => advance({ finished: false }));

  settingsForm.addEventListener("submit", (event) => event.preventDefault());
  settingsForm.addEventListener("change", () => {
    settings = cleanSettings(Object.fromEntries(new FormData(settingsForm)));
    store();
    if (endsAt === null) remaining = phaseSeconds(phase);
    render();
  });

  // A running timer would silently vanish with the tab, so ask before leaving.
  window.addEventListener("beforeunload", (event) => {
    if (endsAt !== null) event.preventDefault();
  });

  window.addEventListener("pagehide", () => stopTracking({ leaving: true }));

  if (taskSelect) {
    if (stored.taskId && taskSelect.querySelector(`option[value="${CSS.escape(String(stored.taskId))}"]`)) {
      taskSelect.value = stored.taskId;
    }
    taskSelect.addEventListener("change", () => {
      store();
      // Switching tasks mid-block books the rest of it on the new one.
      if (endsAt === null) return;
      stopTracking();
      startTracking();
    });
  }

  render();
})();
