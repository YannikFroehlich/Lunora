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
  let activeSuggestionIndex = -1;

  function suggestionOptions() {
    return Array.from(suggestionsPanel.querySelectorAll(".suggestion-option"));
  }

  function setActiveSuggestion(index) {
    const options = suggestionOptions();
    activeSuggestionIndex = options.length ? (index + options.length) % options.length : -1;

    options.forEach((option, optionIndex) => {
      const isActive = optionIndex === activeSuggestionIndex;
      option.classList.toggle("is-active", isActive);
      option.setAttribute("aria-selected", String(isActive));
      if (isActive) {
        searchInput.setAttribute("aria-activedescendant", option.id);
        option.scrollIntoView({ block: "nearest" });
      }
    });

    if (activeSuggestionIndex < 0) {
      searchInput.removeAttribute("aria-activedescendant");
    }
  }

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
    activeSuggestionIndex = -1;
    searchInput.setAttribute("aria-expanded", "false");
    searchInput.removeAttribute("aria-activedescendant");
  }

  function showSuggestionStatus(message) {
    suggestionsPanel.innerHTML = "";
    activeSuggestionIndex = -1;
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

    results.forEach((place, index) => {
      const button = document.createElement("button");
      const icon = document.createElement("i");
      const textWrap = document.createElement("span");
      const name = document.createElement("strong");
      const details = document.createElement("small");

      button.type = "button";
      button.id = `weather-suggestion-${index}`;
      button.className = "suggestion-option";
      button.setAttribute("role", "option");
      button.setAttribute("aria-selected", "false");
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
    setActiveSuggestion(0);
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

  searchInput.addEventListener("keydown", (event) => {
    const options = suggestionOptions();

    if (event.key === "Escape") {
      hideSuggestions();
      return;
    }

    if (suggestionsPanel.hidden || !options.length) {
      return;
    }

    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveSuggestion(activeSuggestionIndex + 1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveSuggestion(activeSuggestionIndex - 1);
    } else if (event.key === "Enter" && activeSuggestionIndex >= 0) {
      event.preventDefault();
      options[activeSuggestionIndex].click();
    }
  });
}

const radarMap = document.querySelector("[data-radar-map]");

if (radarMap) {
  const canvas = radarMap.querySelector("[data-radar-canvas]");
  const baseLayer = radarMap.querySelector("[data-radar-tile-layer]");
  const cloudLayer = radarMap.querySelector("[data-radar-cloud-layer]");
  const rainLayer = radarMap.querySelector("[data-radar-rain-layer]");
  const zoomInButton = radarMap.querySelector("[data-radar-zoom-in]");
  const zoomOutButton = radarMap.querySelector("[data-radar-zoom-out]");
  const fullscreenButton = radarMap.querySelector("[data-radar-fullscreen]");
  const cloudTileUrlTemplate = radarMap.dataset.radarCloudTileUrl || "";
  const rainTileUrlTemplate = radarMap.dataset.radarRainTileUrl || "";
  const hasLiveRadar = radarMap.dataset.radarHasLive === "true";
  const centerLat = Number(radarMap.dataset.radarLat || 52.1984);
  const centerLon = Number(radarMap.dataset.radarLon || 8.5864);
  const minZoom = 5;
  const maxZoom = 10;
  const tileSize = 128;
  let zoom = Math.min(maxZoom, Math.max(minZoom, Number(radarMap.dataset.radarZoom || 7)));

  function lonToTile(lon, currentZoom) {
    return ((lon + 180) / 360) * 2 ** currentZoom;
  }

  function latToTile(lat, currentZoom) {
    const radians = (lat * Math.PI) / 180;
    return ((1 - Math.log(Math.tan(radians) + 1 / Math.cos(radians)) / Math.PI) / 2) * 2 ** currentZoom;
  }

  function wrapTileX(x, maxTile) {
    return ((x % maxTile) + maxTile) % maxTile;
  }

  function radarTileUrl(template, z, x, y) {
    return template.replace(/\/0\/0\/0\.png$/, `/${z}/${x}/${y}.png`);
  }

  function appendTile(layer, src, left, top, className = "radar-tile") {
    const tile = document.createElement("img");
    tile.className = className;
    tile.src = src;
    tile.alt = "";
    tile.loading = "lazy";
    tile.decoding = "async";
    tile.draggable = false;
    tile.style.left = `${left}px`;
    tile.style.top = `${top}px`;
    tile.addEventListener("error", () => tile.remove());
    layer.appendChild(tile);
  }

  function renderRadar() {
    if (!canvas || !baseLayer || !cloudLayer || !rainLayer) {
      return;
    }

    const width = canvas.clientWidth || radarMap.clientWidth || 600;
    const height = canvas.clientHeight || radarMap.clientHeight || 220;
    const maxTile = 2 ** zoom;
    const centerTileX = lonToTile(centerLon, zoom);
    const centerTileY = latToTile(centerLat, zoom);
    const startPixelX = centerTileX * tileSize - width / 2;
    const startPixelY = centerTileY * tileSize - height / 2;
    const startTileX = Math.floor(startPixelX / tileSize);
    const startTileY = Math.floor(startPixelY / tileSize);
    const endTileX = Math.ceil((startPixelX + width) / tileSize);
    const endTileY = Math.ceil((startPixelY + height) / tileSize);

    baseLayer.innerHTML = "";
    cloudLayer.innerHTML = "";
    rainLayer.innerHTML = "";

    for (let tileY = startTileY; tileY <= endTileY; tileY += 1) {
      if (tileY < 0 || tileY >= maxTile) {
        continue;
      }

      for (let tileX = startTileX; tileX <= endTileX; tileX += 1) {
        const wrappedX = wrapTileX(tileX, maxTile);
        const left = Math.round(tileX * tileSize - startPixelX);
        const top = Math.round(tileY * tileSize - startPixelY);
        appendTile(baseLayer, `https://tile.openstreetmap.org/${zoom}/${wrappedX}/${tileY}.png`, left, top);

        if (hasLiveRadar) {
          appendTile(cloudLayer, radarTileUrl(cloudTileUrlTemplate, zoom, wrappedX, tileY), left, top);
          appendTile(rainLayer, radarTileUrl(rainTileUrlTemplate, zoom, wrappedX, tileY), left, top);
        }
      }
    }

    zoomOutButton.disabled = zoom <= minZoom;
    zoomInButton.disabled = zoom >= maxZoom;
  }

  zoomOutButton?.addEventListener("click", () => {
    zoom = Math.max(minZoom, zoom - 1);
    renderRadar();
  });

  zoomInButton?.addEventListener("click", () => {
    zoom = Math.min(maxZoom, zoom + 1);
    renderRadar();
  });

  fullscreenButton?.addEventListener("click", async () => {
    try {
      if (document.fullscreenElement) {
        await document.exitFullscreen();
      } else if (radarMap.requestFullscreen) {
        await radarMap.requestFullscreen();
      }
      window.setTimeout(renderRadar, 80);
    } catch (error) {
      // Fullscreen can be blocked by browser settings; the embedded radar still works.
    }
  });

  window.addEventListener("resize", () => {
    window.requestAnimationFrame(renderRadar);
  });

  document.addEventListener("fullscreenchange", () => {
    window.setTimeout(renderRadar, 80);
  });

  renderRadar();
}
