import { defineConfig } from "vite";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

/**
 * Dev-only convenience (splat-analyzer-plan.md §2, "Persistence"): the
 * Objects panel's "Save to disk" button POSTs to /__annotations/<sceneName>
 * and this middleware writes the body straight to
 * public/annotations/<sceneName>.json, so edits made in-app land directly
 * in the file that ships with the app — no manual "Export JSON" + drag
 * into public/annotations/ round trip needed while iterating.
 *
 * Dev-mode only by construction: `configureServer` is a Vite dev-server
 * hook and is never invoked during `vite build` or `vite preview`.
 */
function annotationsSaveMiddleware() {
  return {
    name: "annotations-save-middleware",
    configureServer(server) {
      server.middlewares.use(async (req, res, next) => {
        const match = req.url?.match(/^\/__annotations\/([a-zA-Z0-9_-]+)$/);
        if (req.method !== "POST" || !match) return next();

        const sceneName = match[1];
        const chunks = [];
        req.on("data", (chunk) => chunks.push(chunk));
        req.on("end", async () => {
          try {
            const body = Buffer.concat(chunks).toString("utf8");
            JSON.parse(body); // validate before writing to disk
            const dir = path.resolve(process.cwd(), "public/annotations");
            await mkdir(dir, { recursive: true });
            await writeFile(path.join(dir, `${sceneName}.json`), body, "utf8");
            res.statusCode = 200;
            res.setHeader("Content-Type", "application/json");
            res.end(JSON.stringify({ ok: true }));
          } catch (err) {
            res.statusCode = 400;
            res.setHeader("Content-Type", "application/json");
            res.end(JSON.stringify({ ok: false, error: String(err) }));
          }
        });
      });
    },
  };
}

export default defineConfig({
  plugins: [annotationsSaveMiddleware()],
});
