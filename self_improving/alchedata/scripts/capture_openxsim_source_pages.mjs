#!/usr/bin/env node

import fs from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(scriptDir, "..");
const outputDir = path.join(root, "reports", "openxsim_command_loop", "assets", "source_pages");
const manifestPath = path.join(root, "reports", "openxsim_command_loop", "assets", "source_page_capture.json");

const sources = [
  {
    source_id: "shaoxiang_awesome_isaac_sim",
    url: "https://github.com/shaoxiang/awesome-isaac-sim",
  },
  {
    source_id: "sjtuyinjie_awesome_isaac_sim",
    url: "https://github.com/sjtuyinjie/awesome-isaac-sim",
  },
  {
    source_id: "video2sim_forge",
    url: "https://github.com/Marvelousp4/video2sim-forge",
  },
  {
    source_id: "neumatex",
    url: "https://nvlabs.github.io/neumatex/",
  },
  {
    source_id: "embodiedgen_v2",
    url: "https://github.com/HorizonRobotics/EmbodiedGen",
  },
  {
    source_id: "roboverse",
    url: "https://roboverseorg.github.io/",
  },
];

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
    const response = await page.goto(source.url, { waitUntil: "domcontentloaded", timeout: 60_000 });
    await page.waitForTimeout(1800);
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.waitForTimeout(400);
    const screenshotName = `${source.source_id}.png`;
    await page.screenshot({ path: path.join(outputDir, screenshotName), fullPage: false });
    captured.push({
      ...source,
      screenshot: `assets/source_pages/${screenshotName}`,
      http_status: response?.status() ?? 0,
      title: await page.title(),
      console_error_count: consoleErrors.length,
      captured_at: "2026-07-14",
    });
    await page.close();
  }
} finally {
  await browser.close();
}

const manifest = {
  schema_version: "alchedata.openxsim_source_capture.v1",
  status: captured.every((row) => row.http_status === 200) ? "pass_source_page_capture" : "fail_source_page_capture",
  source_count: captured.length,
  sources: captured,
};
await fs.writeFile(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`, "utf8");
process.stdout.write(`${JSON.stringify(manifest, null, 2)}\n`);

if (manifest.status !== "pass_source_page_capture") process.exitCode = 1;
