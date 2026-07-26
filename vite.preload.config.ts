import { defineConfig } from "vite";
import path from "node:path";

export default defineConfig({
  build: {
    outDir: "dist/main",
    emptyOutDir: false,
    target: "node20",
    lib: {
      entry: path.resolve(__dirname, "desktop/main/preload.ts"),
      formats: ["cjs"],
      fileName: () => "preload.cjs",
    },
    rollupOptions: {
      external: ["electron"],
    },
  },
});
