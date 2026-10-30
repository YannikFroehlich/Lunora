import { defineConfig } from "vite";

export default defineConfig({
  // Relative, so the module preloads of lazy chunks resolve next to the bundle under /static/
  // instead of at the site root.
  base: "./",
  build: {
    emptyOutDir: true,
    outDir: "app/static/js/bundles",
    sourcemap: false,
    rollupOptions: {
      // notes: the editor; note_public: formula rendering on a publicly shared note.
      input: { notes: "frontend/notes.js", note_public: "frontend/note_public.js" },
      output: {
        entryFileNames: "[name].js",
        chunkFileNames: "chunks/[name]-[hash].js",
        format: "es",
        manualChunks(id) {
          if (!id.includes("node_modules")) return undefined;
          if (id.includes("katex")) return "math-renderer";
          if (id.includes("highlight.js") || id.includes("lowlight")) return "syntax-highlighting";
          if (id.includes("@tiptap") && !id.includes("@tiptap/core") && !id.includes("@tiptap/pm")) {
            return "editor-extensions";
          }
          return "editor-core";
        },
      },
    },
  },
  test: {
    include: ["frontend/**/*.test.js"],
  },
});
