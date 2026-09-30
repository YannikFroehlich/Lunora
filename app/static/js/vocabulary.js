(function () {
  // ---- Pure helpers (covered by frontend/vocabulary.test.js) ------------------------------------

  function stripAccents(value) {
    return value.normalize("NFD").replace(/[̀-ͯ]/g, "");
  }

  function normalizeAnswer(value) {
    return value
      .normalize("NFC")
      .toLowerCase()
      .replace(/\s+/g, " ")
      .replace(/^[\s.,!?;:"'„“”]+|[\s.,!?;:"'„“”]+$/g, "")
      .trim();
  }

  // "Haus, Gebäude" / "(to) run" → every spelling that should count as correct.
  function acceptedAnswers(expected) {
    const variants = new Set();
    expected.split(/[,;/]/).forEach((part) => {
      const trimmed = part.trim();
      if (!trimmed) return;
      variants.add(normalizeAnswer(trimmed));
      if (trimmed.includes("(")) {
        variants.add(normalizeAnswer(trimmed.replace(/\([^)]*\)/g, " ")));
        variants.add(normalizeAnswer(trimmed.replace(/[()]/g, "")));
      }
    });
    variants.delete("");
    return [...variants];
  }

  function editDistance(a, b) {
    const previous = Array.from({ length: b.length + 1 }, (_, index) => index);
    for (let i = 1; i <= a.length; i += 1) {
      let diagonal = previous[0];
      previous[0] = i;
      for (let j = 1; j <= b.length; j += 1) {
        const above = previous[j];
        previous[j] = Math.min(previous[j] + 1, previous[j - 1] + 1, diagonal + (a[i - 1] === b[j - 1] ? 0 : 1));
        diagonal = above;
      }
    }
    return previous[b.length];
  }

  // Returns "correct", "typo" (accepted, but shown as nearly right) or "wrong".
  function checkAnswer(input, expected) {
    const given = normalizeAnswer(input);
    if (!given) return "wrong";
    const answers = acceptedAnswers(expected);
    if (answers.includes(given)) return "correct";
    const plainGiven = stripAccents(given);
    const tolerated = answers.some((answer) => {
      if (stripAccents(answer) === plainGiven) return true;
      const allowed = answer.length >= 9 ? 2 : answer.length >= 4 ? 1 : 0;
      return allowed > 0 && editDistance(plainGiven, stripAccents(answer)) <= allowed;
    });
    return tolerated ? "typo" : "wrong";
  }

  function shuffle(items, random = Math.random) {
    const copy = [...items];
    for (let index = copy.length - 1; index > 0; index -= 1) {
      const swap = Math.floor(random() * (index + 1));
      [copy[index], copy[swap]] = [copy[swap], copy[index]];
    }
    return copy;
  }

  function pickDistractors(cards, current, field, count = 3, random = Math.random) {
    const correct = normalizeAnswer(current[field]);
    const seen = new Set([correct]);
    const options = [];
    shuffle(cards, random).forEach((card) => {
      const value = card[field];
      const key = normalizeAnswer(value);
      if (options.length < count && !seen.has(key)) {
        seen.add(key);
        options.push(value);
      }
    });
    return options;
  }

  globalThis.LunoraVocabulary = {
    normalizeAnswer,
    acceptedAnswers,
    editDistance,
    checkAnswer,
    shuffle,
    pickDistractors,
  };

  if (typeof document === "undefined") return;
  const root = document.querySelector("[data-vocab-practice]");
  if (!root) return;

  // ---- Practice session -------------------------------------------------------------------------

  const SETTINGS_KEY = "lunora-vocab-practice";
  const SPEECH_LANGUAGES = {
    de: "de-DE",
    en: "en-GB",
    fr: "fr-FR",
    es: "es-ES",
    it: "it-IT",
    nl: "nl-NL",
    pl: "pl-PL",
    ru: "ru-RU",
    tr: "tr-TR",
  };
  const REQUEUE_GAP = 3;

  const payload = JSON.parse(document.getElementById("vocab-practice-data").textContent);
  const csrfToken = document.querySelector("meta[name='csrf-token']")?.content || "";
  const reviewUrlTemplate = root.dataset.reviewUrl;
  const setupForm = root.querySelector("[data-vocab-setup]");
  const setupError = root.querySelector("[data-vocab-setup-error]");
  const sessionPanel = root.querySelector("[data-vocab-session]");
  const summaryPanel = root.querySelector("[data-vocab-summary]");
  const promptLabel = root.querySelector("[data-vocab-prompt]");
  const promptLanguage = root.querySelector("[data-vocab-prompt-language]");
  const answerBox = root.querySelector("[data-vocab-answer]");
  const answerLanguage = root.querySelector("[data-vocab-answer-language]");
  const solutionLabel = root.querySelector("[data-vocab-solution]");
  const noteLabel = root.querySelector("[data-vocab-note]");
  const feedbackLabel = root.querySelector("[data-vocab-feedback]");
  const cardBox = root.querySelector("[data-vocab-card]");
  const speakButton = root.querySelector("[data-vocab-speak]");
  const progressBar = root.querySelector("[data-vocab-progress-bar]");
  const progressLabel = root.querySelector("[data-vocab-progress-label]");
  const controls = Object.fromEntries(
    Array.from(root.querySelectorAll("[data-vocab-controls]")).map((node) => [node.dataset.vocabControls, node]),
  );
  const rateRow = root.querySelector("[data-vocab-rate]");
  const flipButton = root.querySelector("[data-vocab-action='flip']");
  const typingInput = root.querySelector("[data-vocab-input]");
  const nextRow = root.querySelector("[data-vocab-next-row]");
  const overrideButton = root.querySelector("[data-vocab-action='override']");
  const nextButton = root.querySelector("[data-vocab-action='next']");

  const allCards = payload.cards;
  const scopes = {
    due: allCards.filter((card) => card.due),
    hard: allCards.filter((card) => card.box === 1),
    all: allCards,
  };
  Object.entries(scopes).forEach(([key, cards]) => {
    const label = root.querySelector(`[data-vocab-count='${key}']`);
    if (label) label.textContent = `(${cards.length})`;
  });

  let settings = null;
  let session = null;

  function readSettings() {
    try {
      return JSON.parse(localStorage.getItem(SETTINGS_KEY)) || {};
    } catch (_error) {
      return {};
    }
  }

  function writeSettings(value) {
    try {
      localStorage.setItem(SETTINGS_KEY, JSON.stringify(value));
    } catch (_error) {
      // Private mode or blocked storage: the defaults simply apply next time.
    }
  }

  function applyStoredSettings() {
    const stored = readSettings();
    ["mode", "direction", "size"].forEach((name) => {
      const input = setupForm.querySelector(`input[name='${name}'][value='${stored[name]}']`);
      if (input) input.checked = true;
    });
    if (!scopes.due.length) setupForm.querySelector("input[name='scope'][value='all']").checked = true;
  }

  function show(panel) {
    [setupForm, sessionPanel, summaryPanel].forEach((node) => (node.hidden = node !== panel));
  }

  function startSession(cards) {
    const size = Number(settings.size);
    const picked = shuffle(cards).slice(0, size > 0 ? size : undefined);
    session = {
      queue: picked.map((card) => ({ card, reversed: directionReversed() })),
      total: picked.length,
      answered: new Set(),
      firstTryCorrect: 0,
      mistakes: new Map(),
      pendingReview: null,
      current: null,
      resolved: false,
    };
    show(sessionPanel);
    nextCard();
  }

  function directionReversed() {
    if (settings.direction === "backward") return true;
    if (settings.direction === "mixed") return Math.random() < 0.5;
    return false;
  }

  function sides(item) {
    const { card, reversed } = item;
    const list = payload.list;
    return reversed
      ? {
          prompt: card.translation,
          answer: card.term,
          answerField: "term",
          promptLang: list.target_language,
          answerLang: list.source_language,
          promptLabel: list.target_label,
          answerLabel: list.source_label,
        }
      : {
          prompt: card.term,
          answer: card.translation,
          answerField: "translation",
          promptLang: list.source_language,
          answerLang: list.target_language,
          promptLabel: list.source_label,
          answerLabel: list.target_label,
        };
  }

  function updateProgress() {
    const done = session.answered.size;
    progressLabel.textContent = `${done} / ${session.total}`;
    progressBar.style.width = `${session.total ? (done / session.total) * 100 : 0}%`;
  }

  function nextCard() {
    flushReview();
    updateProgress();
    session.current = session.queue.shift() || null;
    if (!session.current) {
      finishSession();
      return;
    }
    session.resolved = false;
    const side = sides(session.current);
    promptLabel.textContent = side.prompt;
    promptLanguage.textContent = side.promptLabel;
    answerLanguage.textContent = side.answerLabel;
    solutionLabel.textContent = side.answer;
    noteLabel.textContent = session.current.card.note;
    answerBox.hidden = true;
    feedbackLabel.textContent = "";
    cardBox.dataset.state = "";
    speakButton.hidden = !canSpeak(side.promptLang);
    Object.entries(controls).forEach(([mode, node]) => (node.hidden = mode !== settings.mode));
    nextRow.hidden = true;
    overrideButton.hidden = true;

    if (settings.mode === "flashcards") {
      flipButton.hidden = false;
      rateRow.hidden = true;
      flipButton.focus();
    } else if (settings.mode === "typing") {
      typingInput.value = "";
      typingInput.disabled = false;
      typingInput.lang = side.answerLang || "";
      typingInput.placeholder = `Auf ${side.answerLabel} …`;
      typingInput.focus();
    } else {
      renderChoices(side);
    }
  }

  function renderChoices(side) {
    const options = shuffle([side.answer, ...pickDistractors(allCards, session.current.card, side.answerField)]);
    controls.choice.replaceChildren(
      ...options.map((option, index) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "ghost-button vocab-choice";
        button.dataset.choice = option;
        const key = document.createElement("kbd");
        key.textContent = String(index + 1);
        button.append(key, document.createTextNode(` ${option}`));
        return button;
      }),
    );
    controls.choice.querySelector("button")?.focus();
  }

  function revealAnswer() {
    answerBox.hidden = false;
  }

  function resolve(correct, message) {
    if (!session.current || session.resolved) return;
    session.resolved = true;
    const { card } = session.current;
    const firstAttempt = !session.answered.has(card.id);
    cardBox.dataset.state = correct ? "right" : "wrong";
    feedbackLabel.textContent = message || (correct ? "Richtig!" : "Leider falsch.");

    if (firstAttempt) {
      session.answered.add(card.id);
      if (correct) session.firstTryCorrect += 1;
      // Sent when moving on, so "War doch richtig" can still flip the verdict first.
      session.pendingReview = { card, correct };
    }
    if (!correct) {
      session.mistakes.set(card.id, card);
      // Wrong cards come back a few cards later, until they are answered correctly once.
      session.queue.splice(Math.min(REQUEUE_GAP, session.queue.length), 0, {
        card,
        reversed: session.current.reversed,
      });
    }
  }

  function flushReview() {
    const pending = session?.pendingReview;
    if (!pending) return;
    session.pendingReview = null;
    sendReview(pending.card, pending.correct);
  }

  function sendReview(card, correct) {
    fetch(reviewUrlTemplate.replace("/0/", `/${card.id}/`), {
      method: "POST",
      credentials: "same-origin",
      keepalive: true,
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
      body: JSON.stringify({ correct }),
    })
      .then((response) => response.json())
      .then((data) => {
        if (data.ok) card.box = data.box;
      })
      .catch(() => {
        feedbackLabel.textContent += " (Fortschritt konnte nicht gespeichert werden.)";
      });
  }

  function overrideAsCorrect() {
    const { card } = session.current;
    // Undo the requeue and mistake from resolve(false), then count it as right.
    session.queue = session.queue.filter((item, index) => !(item.card.id === card.id && index <= REQUEUE_GAP));
    session.mistakes.delete(card.id);
    if (session.pendingReview?.card === card) {
      session.pendingReview.correct = true;
      session.firstTryCorrect += 1;
    }
    cardBox.dataset.state = "right";
    feedbackLabel.textContent = "Als richtig gewertet.";
    overrideButton.hidden = true;
    nextButton.focus();
  }

  function showNext() {
    nextRow.hidden = false;
    nextButton.focus();
  }

  function finishSession() {
    show(summaryPanel);
    const total = session.total;
    const right = session.firstTryCorrect;
    const percent = total ? Math.round((right / total) * 100) : 0;
    root.querySelector("[data-vocab-summary-title]").textContent =
      percent >= 90 ? "Super gemacht!" : percent >= 60 ? "Gut gemacht!" : "Weiter üben!";
    root.querySelector("[data-vocab-summary-text]").textContent =
      `${right} von ${total} Vokabeln beim ersten Versuch richtig (${percent} %).`;
    const mistakes = [...session.mistakes.values()];
    root.querySelector("[data-vocab-mistakes]").replaceChildren(
      ...mistakes.map((card) => {
        const item = document.createElement("li");
        const term = document.createElement("strong");
        term.textContent = card.term;
        item.append(term, document.createTextNode(` – ${card.translation}`));
        return item;
      }),
    );
    root.querySelector("[data-vocab-action='retry-wrong']").hidden = !mistakes.length;
  }

  function canSpeak(language) {
    return Boolean(SPEECH_LANGUAGES[language]) && "speechSynthesis" in window;
  }

  function speak(text, language) {
    if (!canSpeak(language)) return;
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = SPEECH_LANGUAGES[language];
    window.speechSynthesis.speak(utterance);
  }

  // ---- Events -----------------------------------------------------------------------------------

  setupForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const data = new FormData(setupForm);
    settings = Object.fromEntries(data.entries());
    writeSettings({ mode: settings.mode, direction: settings.direction, size: settings.size });
    const cards = scopes[settings.scope];
    if (settings.mode === "choice" && allCards.length < 4) {
      setupError.textContent = "Für Multiple Choice braucht die Liste mindestens 4 Vokabeln.";
    } else if (!cards.length) {
      setupError.textContent =
        settings.scope === "due"
          ? "Gerade ist nichts fällig – wähle „Alle“, um trotzdem zu üben."
          : "Keine passenden Vokabeln.";
    } else {
      setupError.textContent = "";
      setupError.hidden = true;
      startSession(cards);
      return;
    }
    setupError.hidden = false;
  });

  controls.typing.addEventListener("submit", (event) => {
    event.preventDefault();
    if (session.resolved) {
      nextCard();
      return;
    }
    const side = sides(session.current);
    const result = checkAnswer(typingInput.value, side.answer);
    typingInput.disabled = true;
    revealAnswer();
    if (result === "correct") resolve(true);
    else if (result === "typo") resolve(true, "Fast richtig – achte auf die Schreibweise.");
    else {
      resolve(false, typingInput.value.trim() ? `Leider falsch. Deine Antwort: „${typingInput.value.trim()}“` : "");
      overrideButton.hidden = !typingInput.value.trim();
    }
    showNext();
  });

  controls.choice.addEventListener("click", (event) => {
    const button = event.target.closest("[data-choice]");
    if (!button || session.resolved) return;
    const side = sides(session.current);
    const correct = normalizeAnswer(button.dataset.choice) === normalizeAnswer(side.answer);
    controls.choice.querySelectorAll("[data-choice]").forEach((option) => {
      option.disabled = true;
      if (normalizeAnswer(option.dataset.choice) === normalizeAnswer(side.answer)) option.classList.add("is-right");
    });
    if (!correct) button.classList.add("is-wrong");
    revealAnswer();
    resolve(correct);
    showNext();
  });

  const ACTIONS = {
    flip: () => {
      revealAnswer();
      flipButton.hidden = true;
      rateRow.hidden = false;
      rateRow.querySelector("[data-vocab-action='right']").focus();
    },
    right: () => {
      resolve(true);
      nextCard();
    },
    wrong: () => {
      resolve(false);
      nextCard();
    },
    next: () => nextCard(),
    override: overrideAsCorrect,
    "retry-wrong": () => startSession([...session.mistakes.values()]),
    restart: () => show(setupForm),
  };

  root.querySelectorAll("[data-vocab-action]").forEach((button) => {
    button.addEventListener("click", () => ACTIONS[button.dataset.vocabAction]());
  });

  speakButton.addEventListener("click", () => {
    const side = sides(session.current);
    speak(side.prompt, side.promptLang);
  });

  document.addEventListener("keydown", (event) => {
    if (sessionPanel.hidden || !session?.current || event.ctrlKey || event.metaKey || event.altKey) return;
    const typing = event.target.closest?.("input, textarea");
    if (settings.mode === "flashcards" && !typing) {
      if (!flipButton.hidden && (event.key === " " || event.key === "Enter")) {
        event.preventDefault();
        ACTIONS.flip();
      } else if (!rateRow.hidden && (event.key === "1" || event.key === "ArrowLeft")) {
        event.preventDefault();
        ACTIONS.wrong();
      } else if (!rateRow.hidden && (event.key === "2" || event.key === "ArrowRight")) {
        event.preventDefault();
        ACTIONS.right();
      }
    } else if (settings.mode === "choice" && !session.resolved && /^[1-4]$/.test(event.key)) {
      controls.choice.querySelectorAll("[data-choice]")[Number(event.key) - 1]?.click();
    } else if (settings.mode === "choice" && session.resolved && event.key === "Enter" && !nextRow.hidden) {
      if (event.target !== nextButton && event.target !== overrideButton) {
        event.preventDefault();
        nextCard();
      }
    }
  });

  window.addEventListener("pagehide", flushReview);
  applyStoredSettings();
})();
