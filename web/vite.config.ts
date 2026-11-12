import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The build lands inside the Python package, which serves it. In development,
// `npm run dev` proxies API calls to `cms-reporter serve` on port 8000.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../src/cms_reporter/static", emptyOutDir: true },
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
});
