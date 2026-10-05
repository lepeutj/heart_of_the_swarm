import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  base: "/workflow-editor/",
  plugins: [react()],
  build: {
    outDir: "../src/heart_of_the_swarm/static/workflow-editor",
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
});
