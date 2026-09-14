import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The bundle is built at image build time and served statically by FastAPI —
// one port, no CORS, no dev server in production (DESIGN.md, Assumptions).
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    // Dev-server only: in the image there is no proxy, same origin serves both.
    proxy: { "/api": "http://localhost:8000" },
  },
});
