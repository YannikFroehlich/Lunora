const visibilityForm = document.querySelector("[data-calendar-visibility-form]");

if (visibilityForm) {
  const submitButton = visibilityForm.querySelector(".calendar-visibility-submit");

  if (submitButton) {
    submitButton.hidden = true;
  }

  visibilityForm.querySelectorAll("input[type='checkbox']").forEach((checkbox) => {
    checkbox.addEventListener("change", () => {
      visibilityForm.requestSubmit();
    });
  });
}
