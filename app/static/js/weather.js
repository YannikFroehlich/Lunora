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

const hourlyCarousel = document.querySelector("[data-hourly-carousel]");

if (hourlyCarousel) {
  const scroller = hourlyCarousel.querySelector("[data-hourly-scroll]");
  const controls = hourlyCarousel.querySelector("[data-hourly-controls]");
  const previousButton = hourlyCarousel.querySelector("[data-hourly-previous]");
  const nextButton = hourlyCarousel.querySelector("[data-hourly-next]");
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  let scrollFrame;

  function updateHourlyControls() {
    if (!scroller || !controls) return;

    const maxScroll = Math.max(0, scroller.scrollWidth - scroller.clientWidth);
    const hasOverflow = maxScroll > 2;
    const canScrollPrevious = hasOverflow && scroller.scrollLeft > 2;
    const canScrollNext = hasOverflow && scroller.scrollLeft < maxScroll - 2;

    controls.hidden = !hasOverflow;
    hourlyCarousel.classList.toggle("can-scroll-previous", canScrollPrevious);
    hourlyCarousel.classList.toggle("can-scroll-next", canScrollNext);
    if (previousButton) previousButton.disabled = !canScrollPrevious;
    if (nextButton) nextButton.disabled = !canScrollNext;
  }

  function scrollHourlyForecast(direction) {
    if (!scroller) return;

    const firstCard = scroller.querySelector(".hour-card");
    const cardWidth = firstCard?.getBoundingClientRect().width || 78;
    const distance = Math.max(cardWidth * 3, scroller.clientWidth * 0.72);
    scroller.scrollBy({
      left: direction * distance,
      behavior: reducedMotion.matches ? "auto" : "smooth",
    });
  }

  previousButton?.addEventListener("click", () => scrollHourlyForecast(-1));
  nextButton?.addEventListener("click", () => scrollHourlyForecast(1));
  scroller?.addEventListener("scroll", () => {
    window.cancelAnimationFrame(scrollFrame);
    scrollFrame = window.requestAnimationFrame(updateHourlyControls);
  }, { passive: true });

  if (scroller && "ResizeObserver" in window) {
    const hourlyResizeObserver = new ResizeObserver(updateHourlyControls);
    hourlyResizeObserver.observe(scroller);
  }

  updateHourlyControls();
}

const weatherMapShell = document.querySelector("[data-weather-map]");

if (weatherMapShell) {
  const mapPanel = weatherMapShell.closest(".weather-map-panel");
  const fullscreenTarget = mapPanel || weatherMapShell;
  const canvas = weatherMapShell.querySelector("[data-weather-map-canvas]");
  const resetButton = weatherMapShell.querySelector("[data-weather-map-reset]");
  const fullscreenButton = weatherMapShell.querySelector("[data-weather-map-fullscreen]");
  const status = weatherMapShell.querySelector("[data-weather-map-status]");
  const legend = weatherMapShell.querySelector("[data-weather-map-legend]");
  const legendTitle = weatherMapShell.querySelector("[data-weather-map-legend-title]");
  const legendUnit = weatherMapShell.querySelector("[data-weather-map-legend-unit]");
  const legendScale = weatherMapShell.querySelector("[data-weather-map-legend-scale]");
  const legendStart = weatherMapShell.querySelector("[data-weather-map-legend-start]");
  const legendEnd = weatherMapShell.querySelector("[data-weather-map-legend-end]");
  const legendHint = weatherMapShell.querySelector("[data-weather-map-legend-hint]");
  const layerButtons = Array.from(mapPanel?.querySelectorAll("[data-weather-map-layer]") || []);
  const layersAvailable = weatherMapShell.dataset.weatherMapAvailable === "true";
  const pointWeatherUrl = weatherMapShell.dataset.weatherMapPointUrl || "";
  const locationName = weatherMapShell.dataset.weatherMapLocation || "Ausgewählter Ort";
  const defaultLayerId = weatherMapShell.dataset.weatherMapDefaultLayer || "temperature";
  const parsedLat = Number(weatherMapShell.dataset.weatherMapLat);
  const parsedLon = Number(weatherMapShell.dataset.weatherMapLon);
  const centerLat = Number.isFinite(parsedLat) ? parsedLat : 52.1984;
  const centerLon = Number.isFinite(parsedLon) ? parsedLon : 8.5864;
  const parsedZoom = Number(weatherMapShell.dataset.weatherMapZoom);
  const initialZoom = Number.isFinite(parsedZoom) ? Math.min(10, Math.max(2, parsedZoom)) : 6;
  const transparentErrorTile =
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='1' height='1'%3E%3C/svg%3E";

  function setStatus(message, isError = false) {
    if (!status) return;
    status.textContent = message;
    status.classList.toggle("is-error", isError);
  }

  function updateLegend(button) {
    if (!button) return;

    const label = button.dataset.weatherMapLabel || "Wetterebene";
    if (legendTitle) legendTitle.textContent = label;
    if (legendUnit) legendUnit.textContent = button.dataset.weatherMapUnit || "";
    if (legendStart) legendStart.textContent = button.dataset.weatherMapLegendStart || "";
    if (legendEnd) legendEnd.textContent = button.dataset.weatherMapLegendEnd || "";
    if (legendHint) legendHint.textContent = button.dataset.weatherMapHint || "";
    if (legendScale) {
      legendScale.className = `weather-map-legend-scale ${button.dataset.weatherMapLegendClass || ""}`.trim();
    }
    if (legend) legend.setAttribute("aria-label", `Legende für ${label}`);
  }

  function setActiveButton(activeButton) {
    layerButtons.forEach((button) => {
      const isActive = button === activeButton;
      button.classList.toggle("is-active", isActive);
      button.setAttribute("aria-pressed", String(isActive));
    });
    updateLegend(activeButton);
  }

  const defaultButton = layerButtons.find(
    (button) => button.dataset.weatherMapLayer === defaultLayerId
  ) || layerButtons[0];
  setActiveButton(defaultButton);

  if (!canvas || typeof window.L === "undefined") {
    layerButtons.forEach((button) => {
      button.disabled = true;
      button.setAttribute("aria-disabled", "true");
    });
    setStatus("Die interaktive Karte konnte nicht geladen werden.", true);
  } else {
    const map = window.L.map(canvas, {
      minZoom: 2,
      maxZoom: 10,
      worldCopyJump: true,
      keyboard: true,
      scrollWheelZoom: true,
      zoomControl: true,
    }).setView([centerLat, centerLon], initialZoom);

    const baseMapLayer = window.L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      minZoom: 2,
      maxZoom: 10,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    });
    baseMapLayer.addTo(map);

    const locationLabel = document.createElement("span");
    locationLabel.textContent = locationName;
    window.L.circleMarker([centerLat, centerLon], {
      className: "weather-map-location-marker",
      radius: 7,
      color: "#fffaf3",
      weight: 3,
      fillColor: "#708a59",
      fillOpacity: 1,
    })
      .addTo(map)
      .bindTooltip(locationLabel, { direction: "top", offset: [0, -7] });

    let activeWeatherLayer = null;
    let selectedPointMarker = null;
    let pointWeatherController = null;
    let resizeTimer = null;

    function formatTemperature(value) {
      const temperature = Number(value);
      if (!Number.isFinite(temperature)) return "–";
      return new Intl.NumberFormat("de-DE", {
        minimumFractionDigits: 1,
        maximumFractionDigits: 1,
      }).format(temperature);
    }

    function createPointPopupContent({ state, weather, message }) {
      const popup = document.createElement("div");
      popup.className = `weather-map-point-content is-${state}`;
      popup.setAttribute("aria-live", "polite");

      const eyebrow = document.createElement("span");
      eyebrow.className = "weather-map-point-eyebrow";
      eyebrow.textContent = state === "loading" ? "Wird geladen" : "Aktuelles Wetter";
      popup.appendChild(eyebrow);

      const title = document.createElement("strong");
      title.className = "weather-map-point-title";
      title.textContent = weather?.location || (state === "error" ? "Temperatur nicht verfügbar" : "Gewählter Ort");
      popup.appendChild(title);

      if (state === "success") {
        const temperature = document.createElement("span");
        temperature.className = "weather-map-point-temperature";
        temperature.textContent = `${formatTemperature(weather.temperature)} °C`;
        popup.appendChild(temperature);

        const description = document.createElement("span");
        description.className = "weather-map-point-description";
        description.textContent = weather.description || "Aktuelles Wetter";
        popup.appendChild(description);

        const feelsLike = document.createElement("small");
        feelsLike.className = "weather-map-point-meta";
        feelsLike.textContent = `Gefühlt ${formatTemperature(weather.feels_like)} °C`;
        popup.appendChild(feelsLike);
      } else {
        const feedback = document.createElement("span");
        feedback.className = "weather-map-point-feedback";
        feedback.textContent = message;
        popup.appendChild(feedback);
      }

      return popup;
    }

    function showPointPopup(latlng, content) {
      if (!selectedPointMarker) {
        selectedPointMarker = window.L.circleMarker(latlng, {
          className: "weather-map-query-marker",
          radius: 8,
          color: "#fffaf3",
          weight: 3,
          fillColor: "#b27a45",
          fillOpacity: 1,
        }).addTo(map);
      } else {
        selectedPointMarker.setLatLng(latlng);
      }

      selectedPointMarker
        .bindPopup(content, {
          className: "weather-map-point-popup",
          closeButton: true,
          minWidth: 205,
          maxWidth: 270,
          offset: [0, -5],
        })
        .openPopup();
    }

    async function loadPointWeather(event) {
      if (!layersAvailable || !pointWeatherUrl) return;

      const latlng = event.latlng.wrap();
      pointWeatherController?.abort();
      pointWeatherController = new AbortController();
      const currentController = pointWeatherController;

      showPointPopup(
        latlng,
        createPointPopupContent({
          state: "loading",
          message: "Temperatur wird abgerufen …",
        })
      );

      const requestUrl = new URL(pointWeatherUrl, window.location.origin);
      requestUrl.searchParams.set("lat", latlng.lat.toFixed(5));
      requestUrl.searchParams.set("lon", latlng.lng.toFixed(5));

      try {
        const response = await fetch(requestUrl, {
          headers: { "X-Requested-With": "XMLHttpRequest" },
          signal: currentController.signal,
        });
        const payload = await response.json();
        if (!response.ok || !payload.ok || !payload.weather) {
          throw new Error(payload.error || "Temperatur konnte nicht geladen werden.");
        }
        if (currentController !== pointWeatherController) return;

        showPointPopup(
          latlng,
          createPointPopupContent({ state: "success", weather: payload.weather })
        );
      } catch (error) {
        if (error.name === "AbortError" || currentController !== pointWeatherController) return;
        const message = error.message || "Temperatur konnte nicht geladen werden.";
        showPointPopup(
          latlng,
          createPointPopupContent({ state: "error", message })
        );
      }
    }

    function refreshMapLayout(redrawLayers = false) {
      window.requestAnimationFrame(() => map.invalidateSize({ pan: false }));
      window.clearTimeout(resizeTimer);
      resizeTimer = window.setTimeout(() => {
        map.invalidateSize({ pan: false });
        if (redrawLayers) {
          baseMapLayer.redraw();
          activeWeatherLayer?.redraw();
        }
      }, 180);
    }

    function leafletTileUrl(template) {
      return template.replace(/\/0\/0\/0\.png$/, "/{z}/{x}/{y}.png");
    }

    function showLayer(button) {
      if (!layersAvailable || !button) return;

      if (activeWeatherLayer) {
        map.removeLayer(activeWeatherLayer);
      }

      setActiveButton(button);
      const label = button.dataset.weatherMapLabel || "Wetterebene";
      const tileUrl = leafletTileUrl(button.dataset.weatherMapTileUrl || "");
      const opacity = Number(button.dataset.weatherMapOpacity || 0.78);
      let loadedTile = false;

      setStatus(`${label} wird geladen …`);
      activeWeatherLayer = window.L.tileLayer(tileUrl, {
        minZoom: 2,
        maxZoom: 10,
        maxNativeZoom: 10,
        opacity: Number.isFinite(opacity) ? opacity : 0.78,
        zIndex: 300,
        keepBuffer: 2,
        errorTileUrl: transparentErrorTile,
        attribution: 'Wetterdaten &copy; <a href="https://openweathermap.org/">OpenWeather</a>',
      });

      activeWeatherLayer.on("tileload", () => {
        if (loadedTile) return;
        loadedTile = true;
        setStatus(button.dataset.weatherMapHint || `${label} wird angezeigt.`);
      });

      activeWeatherLayer.on("tileerror", () => {
        if (!loadedTile) {
          setStatus(`${label} konnte nicht geladen werden. Bitte versuche es erneut.`, true);
        }
      });

      activeWeatherLayer.addTo(map);
    }

    layerButtons.forEach((button) => {
      button.addEventListener("click", () => showLayer(button));
    });

    map.on("popupopen", (event) => {
      const closeButton = event.popup.getElement()?.querySelector(".leaflet-popup-close-button");
      closeButton?.setAttribute("aria-label", "Temperaturanzeige schließen");
    });

    if (layersAvailable && pointWeatherUrl) {
      canvas.classList.add("is-point-selectable");
      map.on("click", loadPointWeather);
    }

    resetButton?.addEventListener("click", () => {
      map.setView([centerLat, centerLon], initialZoom, { animate: true });
      setStatus(`Karte ist wieder auf ${locationName} zentriert.`);
    });

    fullscreenButton?.addEventListener("click", async () => {
      try {
        if (document.fullscreenElement === fullscreenTarget) {
          await document.exitFullscreen();
        } else if (fullscreenTarget.requestFullscreen) {
          await fullscreenTarget.requestFullscreen();
        }
      } catch (error) {
        setStatus("Der Vollbildmodus konnte nicht geöffnet werden.", true);
      }
    });

    document.addEventListener("fullscreenchange", () => {
      const isFullscreen = document.fullscreenElement === fullscreenTarget;
      const icon = fullscreenButton?.querySelector("i");
      if (icon) {
        icon.className = isFullscreen
          ? "fa-solid fa-down-left-and-up-right-to-center"
          : "fa-solid fa-up-right-and-down-left-from-center";
      }
      fullscreenButton?.setAttribute(
        "aria-label",
        isFullscreen ? "Vollbild der Wetterkarte beenden" : "Wetterkarte im Vollbild anzeigen"
      );
      refreshMapLayout(true);
    });

    window.addEventListener("resize", () => {
      refreshMapLayout();
    });

    if ("ResizeObserver" in window) {
      const mapResizeObserver = new ResizeObserver(() => {
        refreshMapLayout();
      });
      mapResizeObserver.observe(weatherMapShell);
    }

    if (layersAvailable) {
      showLayer(defaultButton);
    }
  }
}
