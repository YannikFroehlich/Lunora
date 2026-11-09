(() => {
  const MAX_SUGGESTIONS = 6;

  // Case- and accent-insensitive, so "apfel" finds "Äpfel".
  function normalize(text) {
    return text.normalize("NFD").replace(/\p{M}/gu, "").toLowerCase().trim();
  }

  // Drops a German plural ending, so singular and plural meet: "gurken"/"gurke" -> "gurk",
  // "kiwis" -> "kiwi". Short words keep their s ("reis", "mais", "eis").
  function singular(word) {
    if (word.length > 4 && /[aeiou]s$/.test(word)) return word.slice(0, -1);
    const match = word.match(/^(.{3,}?)(en|n|e)$/);
    return match ? match[1] : word;
  }

  // The same product in singular or plural first, then names starting with the query, a later
  // word starting with it, anywhere; then the same for the query without its plural ending
  // ("Gurken" still finds "Salatgurke"). Alphabetical within each rank.
  function rankSuggestions(suggestions, query, limit = MAX_SUGGESTIONS) {
    const needle = normalize(query);
    if (!needle) return [];
    const needleStem = singular(needle);
    const ranked = [];
    suggestions.forEach((suggestion) => {
      const name = normalize(suggestion.name);
      let rank;
      if (singular(name) === needleStem) {
        rank = -1;
      } else {
        let offset = 0;
        let position = name.indexOf(needle);
        if (position < 0 && needleStem !== needle) {
          offset = 3;
          position = name.indexOf(needleStem);
        }
        if (position < 0) return;
        rank = offset + (position === 0 ? 0 : /\s/.test(name[position - 1]) ? 1 : 2);
      }
      ranked.push({ rank, suggestion });
    });
    ranked.sort((a, b) => a.rank - b.rank || a.suggestion.name.localeCompare(b.suggestion.name, "de"));
    return ranked.slice(0, limit).map((entry) => entry.suggestion);
  }

  globalThis.LunoraShopping = { normalize, singular, rankSuggestions };

  if (typeof document === "undefined") return;
  const root = document.querySelector("[data-shopping-suggest]");
  const dataElement = document.getElementById("shopping-suggestion-data");
  if (!root || !dataElement) return;

  const input = root.querySelector("input");
  const panel = root.querySelector("[role=listbox]");
  const quantityInput = input.form.querySelector('input[name="quantity"]');
  const suggestions = JSON.parse(dataElement.textContent);
  let shown = [];
  // -1: nothing highlighted, so Enter adds exactly what was typed.
  let active = -1;

  function hide() {
    panel.hidden = true;
    panel.replaceChildren();
    shown = [];
    active = -1;
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
  }

  function setActive(index) {
    active = index;
    panel.querySelectorAll("[role=option]").forEach((option, optionIndex) => {
      const isActive = optionIndex === active;
      option.setAttribute("aria-selected", String(isActive));
      if (isActive) {
        input.setAttribute("aria-activedescendant", option.id);
        option.scrollIntoView({ block: "nearest" });
      }
    });
    if (active < 0) input.removeAttribute("aria-activedescendant");
  }

  function choose(suggestion) {
    input.value = suggestion.name;
    hide();
    // Next step is usually the amount; Enter there adds the item.
    (quantityInput || input).focus();
  }

  function render() {
    shown = rankSuggestions(suggestions, input.value);
    if (!shown.length) {
      hide();
      return;
    }
    panel.replaceChildren(
      ...shown.map((suggestion, index) => {
        const option = document.createElement("div");
        const emoji = document.createElement("span");
        const name = document.createElement("strong");
        const aisle = document.createElement("small");
        option.id = `shopping-suggestion-${index}`;
        option.className = "shopping-suggestion";
        option.setAttribute("role", "option");
        option.setAttribute("aria-selected", "false");
        emoji.className = "shopping-emoji";
        emoji.setAttribute("aria-hidden", "true");
        emoji.textContent = suggestion.emoji;
        name.textContent = suggestion.name;
        aisle.textContent = suggestion.aisle;
        option.append(emoji, name, aisle);
        // mousedown would blur the input (and close the panel) before the click lands.
        option.addEventListener("mousedown", (event) => event.preventDefault());
        option.addEventListener("click", () => choose(suggestion));
        return option;
      }),
    );
    panel.hidden = false;
    input.setAttribute("aria-expanded", "true");
    setActive(-1);
  }

  input.addEventListener("input", render);
  input.addEventListener("blur", hide);
  input.addEventListener("keydown", (event) => {
    if (panel.hidden) {
      if (event.key === "ArrowDown" && input.value) {
        render();
        event.preventDefault();
      }
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      const step = event.key === "ArrowDown" ? 1 : -1;
      // Wraps through "nothing highlighted" so the typed text stays reachable.
      setActive(((active + 1 + step + shown.length + 1) % (shown.length + 1)) - 1);
      event.preventDefault();
    } else if (event.key === "Enter" && active >= 0) {
      choose(shown[active]);
      event.preventDefault();
    } else if (event.key === "Escape") {
      hide();
      event.preventDefault();
    }
  });
})();
