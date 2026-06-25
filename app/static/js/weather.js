const weatherForm = document.querySelector("[data-weather-search]");

if (weatherForm) {
  const searchInput = weatherForm.querySelector('input[name="q"]');
  const latInput = weatherForm.querySelector("[data-weather-lat]");
  const lonInput = weatherForm.querySelector("[data-weather-lon]");
  const nameInput = weatherForm.querySelector("[data-weather-name]");
  const detailsInput = weatherForm.querySelector("[data-weather-details]");
  const labelInput = weatherForm.querySelector("[data-weather-label]");
  const suggestionsPanel = weatherForm.querySelector("#weather-suggestions");
  const currentLocationButton = weatherForm.querySelector("[data-current-location]");
  const suggestUrl = weatherForm.dataset.suggestUrl;

  let selectedLabel = searchInput.value;
  let debounceTimer;
  let activeRequest;

  function clearSelectedLocation() {
    latInput.value = "";
    lonInput.value = "";
    nameInput.value = "";
    detailsInput.value = "";
    labelInput.value = "";
  }

  function hideSuggestions() {
    suggestionsPanel.hidden = true;
    suggestionsPanel.innerHTML = "";
    searchInput.setAttribute("aria-expanded", "false");
  }

  function showSuggestionStatus(message) {
    suggestionsPanel.innerHTML = "";
    const status = document.createElement("div");
    status.className = "suggestion-status";
    status.textContent = message;
    suggestionsPanel.appendChild(status);
    suggestionsPanel.hidden = false;
    searchInput.setAttribute("aria-expanded", "true");
  }

  function chooseSuggestion(place) {
    selectedLabel = place.label;
    searchInput.value = place.label;
    latInput.value = place.lat;
    lonInput.value = place.lon;
    nameInput.value = place.name;
    detailsInput.value = place.details || `${place.state ? `${place.state}, ` : ""}${place.country}`;
    labelInput.value = place.label;
    hideSuggestions();
    weatherForm.requestSubmit();
  }

  function renderSuggestions(results) {
    suggestionsPanel.innerHTML = "";

    if (!results.length) {
      showSuggestionStatus("Keine passenden Orte gefunden.");
      return;
    }

    results.forEach((place) => {
      const button = document.createElement("button");
      const icon = document.createElement("i");
      const textWrap = document.createElement("span");
      const name = document.createElement("strong");
      const details = document.createElement("small");

      button.type = "button";
      button.className = "suggestion-option";
      button.setAttribute("role", "option");
      icon.className = "fa-solid fa-location-dot";
      icon.setAttribute("aria-hidden", "true");
      name.textContent = place.name;
      details.textContent = `${place.state ? `${place.state}, ` : ""}${place.country}`;

      textWrap.append(name, details);
      button.append(icon, textWrap);
      button.addEventListener("click", () => chooseSuggestion(place));
      suggestionsPanel.appendChild(button);
    });

    suggestionsPanel.hidden = false;
    searchInput.setAttribute("aria-expanded", "true");
  }

  async function fetchSuggestions(query) {
    if (activeRequest) activeRequest.abort();
    activeRequest = new AbortController();

    const response = await fetch(`${suggestUrl}?q=${encodeURIComponent(query)}`, {
      signal: activeRequest.signal,
      headers: { "X-Requested-With": "XMLHttpRequest" },
    });
    const data = await response.json();
    renderSuggestions(data.results || []);
  }

  searchInput.addEventListener("input", () => {
    const query = searchInput.value.trim();

    if (query !== selectedLabel) {
      clearSelectedLocation();
    }

    window.clearTimeout(debounceTimer);

    if (query.length < 2) {
      hideSuggestions();
      return;
    }

    debounceTimer = window.setTimeout(() => {
      showSuggestionStatus("Suche Orte ...");
      fetchSuggestions(query).catch((error) => {
        if (error.name !== "AbortError") {
          showSuggestionStatus("Vorschläge konnten gerade nicht geladen werden.");
        }
      });
    }, 240);
  });

  weatherForm.addEventListener("submit", () => {
    if (searchInput.value !== selectedLabel) {
      clearSelectedLocation();
    }
  });

  document.addEventListener("click", (event) => {
    if (!weatherForm.contains(event.target)) {
      hideSuggestions();
    }
  });

  currentLocationButton?.addEventListener("click", () => {
    if (!navigator.geolocation) return;

    currentLocationButton.disabled = true;
    navigator.geolocation.getCurrentPosition(
      (position) => {
        searchInput.value = "Mein Standort";
        selectedLabel = "Mein Standort";
        latInput.value = position.coords.latitude;
        lonInput.value = position.coords.longitude;
        nameInput.value = "Mein Standort";
        detailsInput.value = "";
        labelInput.value = "Mein Standort";
        weatherForm.requestSubmit();
      },
      () => {
        currentLocationButton.disabled = false;
      },
      { enableHighAccuracy: false, timeout: 8000 }
    );
  });
}
