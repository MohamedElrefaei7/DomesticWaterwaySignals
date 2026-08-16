/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    // The API runs on loopback:8000 from the host venv until Phase 10 puts Caddy in front.
    // Proxying in dev means `client.ts` uses the same relative paths it will use in production,
    // so the built bundle is not exercising a different URL shape than the one that was tested.
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./tests/setup.ts"],
    include: ["tests/**/*.test.ts", "tests/**/*.test.tsx"],
  },
});
