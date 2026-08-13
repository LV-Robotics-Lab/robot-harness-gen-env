#!/usr/bin/env node

import fs from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(scriptDir, "..");
const reportRoot = path.resolve(process.argv[2] ?? path.join(root, "reports", "openxsim_command_loop"));
const entryPath = path.join(reportRoot, "index.html");
const entryUrl = pathToFileURL(entryPath).href;
const qaDir = path.join(reportRoot, "qa");
const logPath = path.join(reportRoot, "assets", "browser_qa.txt");

const expected = {
  images: 13,
  videos: 4,
  commands: 6,
  adapters: 8,
  acceptanceRows: 8,
};

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

async function verifyLocalReferences(references) {
  const paths = new Set();
  for (const reference of references) {
    if (!reference || reference.startsWith("#")) continue;
    const resolved = new URL(reference, entryUrl);
    if (resolved.protocol !== "file:") continue;
    paths.add(fileURLToPath(resolved));
  }
  for (const localPath of paths) {
    const stat = await fs.stat(localPath);
    assert(stat.isFile() && stat.size > 0, `empty local reference: ${localPath}`);
  }
  return paths.size;
}

async function inspectPage(page) {
  await page.goto(entryUrl, { waitUntil: "load" });
  await page.waitForFunction(
    () => [...document.images].every((image) => image.complete) && [...document.querySelectorAll("video")].every((video) => video.readyState >= 1),
    null,
    { timeout: 30_000 },
  );

  return page.evaluate(() => {
    const images = [...document.images].map((image) => ({
      src: image.getAttribute("src"),
      width: image.naturalWidth,
      height: image.naturalHeight,
    }));
    const videos = [...document.querySelectorAll("video")].map((video) => ({
      src: video.getAttribute("src"),
      poster: video.getAttribute("poster"),
      duration: video.duration,
      width: video.videoWidth,
      height: video.videoHeight,
      readyState: video.readyState,
    }));
    const references = [...document.querySelectorAll("img[src], video[src], video[poster], a[href]")].flatMap((element) => {
      const values = [];
      for (const attribute of ["src", "poster", "href"]) {
        if (element.hasAttribute(attribute)) values.push(element.getAttribute(attribute));
      }
      return values;
    });
    const clippedStatuses = [...document.querySelectorAll(".candidate > header .status")].filter((status) => {
      const header = status.closest("header");
      const statusRect = status.getBoundingClientRect();
      const headerRect = header.getBoundingClientRect();
      return statusRect.right > headerRect.right + 1 || statusRect.bottom > headerRect.bottom + 1;
    }).length;
    const sectionRowCount = (heading) => {
      const section = [...document.querySelectorAll("section.block")].find(
        (candidate) => candidate.querySelector("h2")?.textContent.trim() === heading,
      );
      return section?.querySelectorAll("tbody tr").length ?? 0;
    };
    return {
      images,
      videos,
      references,
      commands: document.querySelectorAll(".command-row").length,
      adapters: sectionRowCount("Open X Sim Adapter Matrix"),
      acceptanceRows: sectionRowCount("Acceptance Audit"),
      horizontalOverflow: document.documentElement.scrollWidth - window.innerWidth,
      clippedStatuses,
    };
  });
}

await fs.mkdir(qaDir, { recursive: true });
await fs.mkdir(path.dirname(logPath), { recursive: true });

const browser = await chromium.launch({
  headless: true,
  args: ["--disable-gpu", "--disable-dev-shm-usage"],
});
const consoleErrors = [];
const pageErrors = [];
let desktop;
let mobile;
let checkedReferences = 0;

try {
  const desktopContext = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
    deviceScaleFactor: 1,
    colorScheme: "light",
  });
  const desktopPage = await desktopContext.newPage();
  desktopPage.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  desktopPage.on("pageerror", (error) => pageErrors.push(error.message));
  desktop = await inspectPage(desktopPage);
  checkedReferences = await verifyLocalReferences(desktop.references);
  await desktopPage.screenshot({ path: path.join(qaDir, "desktop-viewport.png") });
  await desktopPage.screenshot({ path: path.join(qaDir, "desktop-full.png"), fullPage: true });
  const desktopSections = {
    "desktop-isaac-intake.png": "AgenticSim Isaac Intake",
    "desktop-benchmarks.png": "RoboTwin Loop Smokes",
    "desktop-adapter-matrix.png": "Open X Sim Adapter Matrix",
    "desktop-fallbacks.png": "Fallback Gates",
  };
  for (const [fileName, heading] of Object.entries(desktopSections)) {
    const section = desktopPage.locator("section.block", { has: desktopPage.getByRole("heading", { name: heading, exact: true }) });
    assert((await section.count()) === 1, `section not unique: ${heading}`);
    await section.screenshot({ path: path.join(qaDir, fileName) });
  }
  await desktopContext.close();

  const mobileContext = await browser.newContext({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 1,
    colorScheme: "light",
    isMobile: true,
  });
  const mobilePage = await mobileContext.newPage();
  mobilePage.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  mobilePage.on("pageerror", (error) => pageErrors.push(error.message));
  mobile = await inspectPage(mobilePage);
  await mobilePage.screenshot({ path: path.join(qaDir, "mobile-viewport.png") });
  await mobilePage.screenshot({ path: path.join(qaDir, "mobile-full.png"), fullPage: true });
  const mobileIsaac = mobilePage.locator("section.block", {
    has: mobilePage.getByRole("heading", { name: "AgenticSim Isaac Intake", exact: true }),
  });
  assert((await mobileIsaac.count()) === 1, "mobile Isaac intake section not unique");
  await mobileIsaac.screenshot({ path: path.join(qaDir, "mobile-isaac-intake.png") });
  await mobileContext.close();
} finally {
  await browser.close();
}

assert(desktop.images.length === expected.images, `desktop image count: ${desktop.images.length}`);
assert(mobile.images.length === expected.images, `mobile image count: ${mobile.images.length}`);
assert(desktop.images.every((image) => image.width > 0 && image.height > 0), "desktop has an undecoded image");
assert(mobile.images.every((image) => image.width > 0 && image.height > 0), "mobile has an undecoded image");
assert(desktop.videos.length === expected.videos, `desktop video count: ${desktop.videos.length}`);
assert(mobile.videos.length === expected.videos, `mobile video count: ${mobile.videos.length}`);
for (const [viewport, media] of [["desktop", desktop], ["mobile", mobile]]) {
  assert(media.videos.every((video) => video.readyState >= 1), `${viewport} has video metadata failure`);
  assert(media.videos.every((video) => video.width > 0 && video.height > 0), `${viewport} has undecoded video dimensions`);
  assert(media.videos.every((video) => video.poster), `${viewport} has a video without a poster`);
  assert(media.videos.every((video) => Number.isFinite(video.duration) && video.duration >= 2), `${viewport} has a video shorter than 2 seconds`);
  assert(media.horizontalOverflow <= 1, `${viewport} horizontal overflow: ${media.horizontalOverflow}px`);
  assert(media.clippedStatuses === 0, `${viewport} has clipped candidate status badges`);
  assert(media.commands === expected.commands, `${viewport} command count: ${media.commands}`);
}
assert(desktop.acceptanceRows === expected.acceptanceRows, `acceptance row count: ${desktop.acceptanceRows}`);
assert(desktop.adapters === expected.adapters, `adapter row count: ${desktop.adapters}`);
assert(consoleErrors.length === 0, `console errors: ${consoleErrors.join(" | ")}`);
assert(pageErrors.length === 0, `page errors: ${pageErrors.join(" | ")}`);

const log = [
  "status=PASS",
  `entry=${entryPath}`,
  "viewports=desktop:1440x1000,mobile:390x844",
  `images=${desktop.images.length}`,
  `videos=${desktop.videos.length}`,
  `video_durations=${desktop.videos.map((video) => video.duration.toFixed(3)).join(",")}`,
  `commands=${desktop.commands}`,
  `adapters=${desktop.adapters}`,
  `acceptance_rows=${desktop.acceptanceRows}`,
  `checked_references=${checkedReferences}`,
  `desktop_horizontal_overflow=${desktop.horizontalOverflow}`,
  `mobile_horizontal_overflow=${mobile.horizontalOverflow}`,
  `clipped_statuses=${desktop.clippedStatuses + mobile.clippedStatuses}`,
  `console_errors=${consoleErrors.length}`,
  `page_errors=${pageErrors.length}`,
].join("\n");
await fs.writeFile(logPath, `${log}\n`, "utf8");
process.stdout.write(`${log}\n`);
