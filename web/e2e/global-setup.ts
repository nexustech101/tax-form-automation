/**
 * Starts `python -m tests.e2e.stack` (API on fake models, fresh temporary data directory, built dashboard from
 * web/dist) and waits for GET /health to report "ok" (Docling loads first). The keys are passed to the tests
 * as E2E_ADMIN_KEY / E2E_CLIENT_KEY. Returns the teardown that stops the stack.
 */
import { spawn, spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { API_PORT } from "../playwright.config";

const ROOT = resolve(import.meta.dirname, "..", "..");
const MODELS_PORT = Number(process.env.E2E_MODELS_PORT ?? API_PORT + 1);
const WINDOWS = process.platform === "win32";

export default async function globalSetup() {
  if (!existsSync(join(ROOT, "web", "dist", "index.html"))) throw new Error("Build first: npm run build (npm run e2e does)");
  const dir = mkdtempSync(join(tmpdir(), "tda-e2e-"));
  const state = join(dir, "state.json");
  const stack = spawn(
    "uv",
    ["run", "--no-sync", "python", "-m", "tests.e2e.stack", "--api-port", String(API_PORT), "--models-port", String(MODELS_PORT),
      "--data", join(dir, "data"), "--state", state],
    // POSIX: its own process group, so the teardown stops uv and the Python process it started.
    { cwd: ROOT, env: { HF_HUB_OFFLINE: "1", ...process.env }, stdio: ["ignore", "ignore", "inherit"], detached: !WINDOWS },
  );
  const stop = () => {
    try {
      if (WINDOWS) spawnSync("taskkill", ["/pid", String(stack.pid), "/T", "/F"], { stdio: "ignore" });
      else process.kill(-stack.pid!, "SIGTERM");
    } catch {
      // already gone
    }
    rmSync(dir, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
  };
  try {
    await waitFor(async () => existsSync(state), stack, "the stack's state file");
    const keys = JSON.parse(readFileSync(state, "utf8")) as { admin_key: string; client_key: string };
    process.env.E2E_ADMIN_KEY = keys.admin_key;
    process.env.E2E_CLIENT_KEY = keys.client_key;
    await waitFor(async () => {
      const r = await fetch(`http://127.0.0.1:${API_PORT}/health`).catch(() => null);
      return r?.ok === true && ((await r.json()) as { data: { status: string } }).data.status === "ok";
    }, stack, "GET /health status ok");
  } catch (error) {
    stop();
    throw error;
  }
  return stop;
}

async function waitFor(ready: () => Promise<boolean>, stack: ReturnType<typeof spawn>, what: string, seconds = 180) {
  for (let i = 0; i < seconds * 2; i++) {
    if (stack.exitCode !== null) throw new Error(`tests.e2e.stack exited (${stack.exitCode}) before ${what}`);
    if (await ready()) return;
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(`Timed out waiting for ${what}`);
}
