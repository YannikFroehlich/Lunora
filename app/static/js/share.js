// Fills the share page from its URL fragment (#title=…&text=…&url=…), put there by the service
// worker for the app's share sheet, or by hand (an iOS shortcut, a bookmarklet). A fragment never
// reaches the server, so the shared text stays out of its logs.
(() => {
  const form = document.querySelector("[data-share-form]");
  if (!form) return;
  const field = (name) => form.querySelector(`[data-share-field="${name}"]`);

  if (window.location.hash.length > 1) {
    const shared = new URLSearchParams(window.location.hash.slice(1));
    window.history.replaceState(null, "", window.location.pathname);
    let title = (shared.get("title") || "").trim();
    let text = (shared.get("text") || "").trim();
    let url = (shared.get("url") || "").trim();
    // Many Android apps put the link into the text instead of the url field.
    const linkInText = !url && text.match(/https?:\/\/\S+/);
    if (linkInText) {
      url = linkInText[0];
      text = text.replace(url, "").trim();
    }
    // Tasks and bookmarks need a title; a short first line of the text becomes one.
    const [firstLine, ...rest] = text.split("\n");
    if (!title && firstLine.length <= 200) {
      title = firstLine.trim();
      text = rest.join("\n").trim();
    }
    field("title").value = title;
    field("text").value = text;
    field("url").value = url;
  }

  const updateLinkButtons = () => {
    const missing = !field("url").value.trim();
    form.querySelectorAll("[data-share-needs-link]").forEach((button) => {
      button.disabled = missing;
    });
  };
  field("url").addEventListener("input", updateLinkButtons);
  updateLinkButtons();
})();
