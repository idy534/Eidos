import { fileURLToPath, URL } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";


export default defineConfig({
  root: fileURLToPath(new URL("./desktop/renderer", import.meta.url)),
  base: "./",
  plugins: [react()],
  build: {
    outDir: fileURLToPath(new URL("./dist/renderer", import.meta.url)),
    // dist/renderer also contains generated file-icons outside Vite's assets directory.
    emptyOutDir: false,
  },
});
