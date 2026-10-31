import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const API = process.env.API_URL ?? "http://127.0.0.1:8000";
const proxied = ["/v1", "/auth", "/api-keys", "/health", "/docs", "/redoc", "/openapi.json"];

export default defineConfig({
  plugins: [react()],
  server: { proxy: Object.fromEntries(proxied.map((path) => [path, API])) },
  build: { outDir: "dist", sourcemap: true },
  test: { include: ["src/**/*.test.ts"], environment: "node" },
});
