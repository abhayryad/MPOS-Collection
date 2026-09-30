import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: `npm run dev` on :5173 proxies /api to the FastAPI server on :8028.
// Prod: `npm run build` writes dist/, which FastAPI serves at /.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": "http://localhost:8028" },
  },
  build: { outDir: "dist", emptyOutDir: true },
});
