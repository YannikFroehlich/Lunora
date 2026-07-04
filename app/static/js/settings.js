// Keep the custom profile image picker label in sync with the selected file.
document.querySelectorAll("[data-file-input]").forEach((input) => {
  const fileNameLabel = document.querySelector(`[data-file-name-for="${input.id}"]`);

  if (!fileNameLabel) {
    return;
  }

  input.addEventListener("change", () => {
    const fileName = input.files && input.files.length > 0
      ? input.files[0].name
      : "Keine neue Datei ausgewählt";

    fileNameLabel.textContent = fileName;
    fileNameLabel.classList.toggle("has-file", Boolean(input.files && input.files.length));
  });
});

// Keep custom switches visually in sync before the preferences form is saved.
document.querySelectorAll(".switch input[type='checkbox']").forEach((input) => {
  const switchLabel = input.closest(".switch");

  if (!switchLabel) {
    return;
  }

  const syncSwitchState = () => {
    switchLabel.classList.toggle("is-on", input.checked);
  };

  syncSwitchState();
  input.addEventListener("change", syncSwitchState);
});
