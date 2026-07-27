import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: [path.resolve(__dirname, "desktop/renderer/test/setup.ts")],
    include: ["desktop/renderer/**/*.behavior.test.{ts,tsx}"],
    globals: true,
  },
});
