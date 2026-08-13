#!/usr/bin/env node

import crypto from "node:crypto";
import fs from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(scriptDir, "..");
const reportRoot = path.join(root, "reports", "text2env_literature_review");
const outputDir = path.join(reportRoot, "assets", "source_pages");
const manifestPath = path.join(reportRoot, "assets", "source_page_capture.json");

const allSources = [
  ["scenesmith", "https://scenesmith.github.io/", "scenesmith.png"],
  ["robotwin_2", "https://robotwin-platform.github.io/", "robotwin_2.png"],
  ["robotwin_digital_twins", "https://arxiv.org/abs/2504.13059", "robotwin_digital_twins.png"],
  ["robogen", "https://robogen-ai.github.io/", "robogen.png"],
  ["fate", "https://arxiv.org/abs/2603.01505", "fate.png"],
  ["vlmbench", "https://github.com/UCSB-AI/VLMbench", "vlmbench.png"],
  ["realm", "https://martin-sedlacek.com/realm/", "realm.png"],
  ["aspire", "https://research.nvidia.com/labs/gear/aspire/", "aspire.png", 760],
  ["enpire", "https://research.nvidia.com/labs/gear/enpire/", "enpire.png"],
  ["articraft", "https://articraft3d.github.io/", "articraft.png", 820],
  ["embodiedgen_v2", "https://github.com/HorizonRobotics/EmbodiedGen", "embodiedgen_v2.png"],
  ["roboverse", "https://roboverseorg.github.io/", "roboverse.png"],
  ["generative_worlds_sim2real_rl", "https://arxiv.org/abs/2603.18532", "generative_worlds_sim2real_rl.png"],
  ["dipo", "https://github.com/RQ-Wu/DIPO", "dipo.png"],
  ["three_d_fixer", "https://zx-yin.github.io/3dfixer/", "three_d_fixer.png"],
  ["uni3r", "https://github.com/HorizonRobotics/Uni3R", "uni3r.png"],
  ["gausstr", "https://github.com/hustvl/GaussTR", "gausstr.png"],
  ["dirtnet", "https://publica.fraunhofer.de/entities/publication/a98dd796-7363-469d-9fc2-030e157ba603", "dirtnet.png"],
  ["instancenet", "https://publica.fraunhofer.de/handle/publica/412776", "instancenet.png"],
].map(([source_id, url, screenshotName, scrollY = 0]) => ({ source_id, url, screenshotName, scrollY }));
const requestedSourceIds = new Set(
  (process.env.ONLY_SOURCE_IDS ?? "").split(",").filter(Boolean),
);
const sources = requestedSourceIds.size
  ? allSources.filter((source) => requestedSourceIds.has(source.source_id))
  : allSources;

await fs.mkdir(outputDir, { recursive: true });
const browser = await chromium.launch({
  headless: true,
  args: ["--disable-gpu", "--disable-dev-shm-usage"],
});
const context = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  deviceScaleFactor: 1,
  colorScheme: "light",
});

const captured = [];
try {
  for (const source of sources) {
    const page = await context.newPage();
    const consoleErrors = [];
    page.on("console", (message) => {
      if (message.type() === "error") consoleErrors.push(message.text());
    });
    const response = await page.goto(source.url, {
      waitUntil: "domcontentloaded",
      timeout: 60_000,
    });
    await page.waitForTimeout(1800);
    await page.evaluate((scrollY) => window.scrollTo(0, scrollY), source.scrollY);
    await page.waitForTimeout(300);
    const screenshotPath = path.join(outputDir, source.screenshotName);
    await page.screenshot({ path: screenshotPath, fullPage: false });
    const screenshot = await fs.readFile(screenshotPath);
    captured.push({
      source_id: source.source_id,
      url: source.url,
      final_url: page.url(),
      http_status: response?.status() ?? 0,
      title: await page.title(),
      screenshot: `assets/source_pages/${source.screenshotName}`,
      screenshot_sha256: crypto.createHash("sha256").update(screenshot).digest("hex"),
      screenshot_bytes: screenshot.byteLength,
      console_error_count: consoleErrors.length,
    });
    await page.close();
  }
} finally {
  await browser.close();
}

let manifestSources = captured;
if (requestedSourceIds.size) {
  const previous = JSON.parse(await fs.readFile(manifestPath, "utf8"));
  const byId = new Map(previous.sources.map((row) => [row.source_id, row]));
  for (const row of captured) byId.set(row.source_id, row);
  manifestSources = allSources.map((source) => byId.get(source.source_id));
}

const manifest = {
  schema_version: "pearl.text2env_source_page_capture.v1",
  captured_at: "2026-07-14",
  status: manifestSources.every((row) => row.http_status >= 200 && row.http_status < 400)
    ? "pass_source_page_capture"
    : "fail_source_page_capture",
  source_count: manifestSources.length,
  sources: manifestSources,
};
await fs.writeFile(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`, "utf8");
process.stdout.write(`${JSON.stringify(manifest, null, 2)}\n`);

if (manifest.status !== "pass_source_page_capture") process.exitCode = 1;
