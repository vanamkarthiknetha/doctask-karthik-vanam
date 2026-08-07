import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  base: "/ui/",
  server: {
    proxy: {
      "/piles": "http://localhost:8000",
      "/runs": "http://localhost:8000",
      "/items": "http://localhost:8000",
      "/health": "http://localhost:8000",
    },
  },
});
