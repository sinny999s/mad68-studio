import { readFileSync } from "node:fs"
import { sveltekit } from "@sveltejs/kit/vite"
import tailwindcss from "@tailwindcss/vite"
import { defineConfig } from "vite"

const { version } = JSON.parse(readFileSync("package.json", "utf-8"))

export default defineConfig({
  plugins: [tailwindcss(), sveltekit()],
  define: {
    // The app's own version, for the update check
    __APP_VERSION__: JSON.stringify(version),
  },
})
