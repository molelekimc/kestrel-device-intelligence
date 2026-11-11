import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the API runs on :8000 (kestrel-app); in production FastAPI serves dist/.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
  build: { outDir: "dist", sourcemap: false },
  test: { environment: "node" },
});
