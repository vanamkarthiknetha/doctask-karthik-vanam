import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: "/ui/",
  resolve: {
    alias: { "@": path.resolve(root, "src") },
  },
  server: {
    proxy: {
      "/piles": "http://localhost:8000",
      "/runs": "http://localhost:8000",
      "/items": "http://localhost:8000",
      "/health": "http://localhost:8000",
      "/corpus": "http://localhost:8000",
    },
  },
});
