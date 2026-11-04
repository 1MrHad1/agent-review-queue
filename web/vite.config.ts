import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    // The API runs on Django's dev server; proxying keeps the browser on one
    // origin so there is no CORS middleware to configure for a local demo.
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
});
