// Renders the formulas of a publicly shared note (note_public.html). The page only loads this entry
// when the note contains formulas; KaTeX itself comes from the shared, lazily loaded math chunk.
const formulas = document.querySelectorAll("[data-latex]");

if (formulas.length) {
  const { default: katex } = await import("katex");
  formulas.forEach((element) => {
    katex.render(element.dataset.latex, element, {
      displayMode: element.dataset.display === "block",
      throwOnError: false,
    });
  });
}
