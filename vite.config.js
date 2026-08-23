import { defineConfig } from "vite";

export default defineConfig({
  build: {
    emptyOutDir: true,
    lib: {
      entry: "frontend/notes.js",
      formats: ["iife"],
      name: "LunoraNotes",
      fileName: () => "notes.js",
    },
    outDir: "app/static/js/bundles",
    sourcemap: false,
  },
  test: {
    include: ["frontend/**/*.test.js"],
  },
});
