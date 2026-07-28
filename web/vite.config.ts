import { defineConfig } from "vite";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [tailwindcss()],
  // The exporter mounts StaticFiles at the URL "/static" pointing *at* the
  // asset directory, so "/static/app.js" resolves to "<dir>/app.js" - there is
  // no nested "static" folder on disk. Assets therefore go in the output root
  // (assetsDir: "static") while their URLs get the "/static/" prefix from `base`.
  // Getting this pair wrong 404s every asset while the page itself still
  // loads, which looks like a CSS bug rather than a path bug.
  base: "./",
  build: {
    outDir: "dist",
    emptyOutDir: true,
    assetsDir: "static",
  },
});
