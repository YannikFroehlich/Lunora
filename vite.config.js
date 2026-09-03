import { defineConfig } from "vite";

export default defineConfig({
  build: {
    emptyOutDir: true,
    outDir: "app/static/js/bundles",
    sourcemap: false,
    rollupOptions: {
      input: "frontend/notes.js",
      output: {
        entryFileNames: "notes.js",
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
