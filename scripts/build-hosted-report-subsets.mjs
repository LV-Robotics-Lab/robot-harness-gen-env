#!/usr/bin/env node

import { createHash } from "node:crypto";
import { readdir, readFile, rm, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const reportsRoot = path.join(root, "public", "reports");
const reportKeys = ["sceneagent", "text2env", "openxsim", "harness"];
const apply = process.argv.includes("--apply");
const attributePattern =
  /\b(?:src|href|poster|data-src)\s*=\s*["']([^"']+)["']/gi;

async function walk(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const absolute = path.join(directory, entry.name);
    if (entry.isDirectory()) files.push(...(await walk(absolute)));
    else if (entry.isFile()) files.push(absolute);
  }
  return files;
}

function inside(parent, candidate) {
  const relative = path.relative(parent, candidate);
  return relative !== "" && !relative.startsWith("..") && !path.isAbsolute(relative);
}

function candidateFromReference(reportKey, reportRoot, sourceFile, rawReference) {
  const reference = rawReference.split("#", 1)[0].split("?", 1)[0];
  if (
    !reference ||
    reference.startsWith("#") ||
    /^(?:https?:|data:|mailto:|javascript:)/i.test(reference)
  ) {
    return null;
  }

  let decoded;
  try {
    decoded = decodeURIComponent(reference);
  } catch {
    decoded = reference;
  }

  const absolute = decoded.startsWith(`/reports/${reportKey}/`)
    ? path.join(reportRoot, decoded.slice(`/reports/${reportKey}/`.length))
    : decoded.startsWith("/")
      ? null
      : path.resolve(path.dirname(sourceFile), decoded);

  return absolute && (absolute === reportRoot || inside(reportRoot, absolute))
    ? absolute
    : null;
}

async function collectReferences(reportKey, reportRoot, sourceFile, keep, queue) {
  const text = await readFile(sourceFile, "utf8");
  for (const match of text.matchAll(attributePattern)) {
    const candidate = candidateFromReference(
      reportKey,
      reportRoot,
      sourceFile,
      match[1],
    );
    if (!candidate) continue;
    try {
      const candidateStat = await stat(candidate);
      const files = candidateStat.isDirectory()
        ? await walk(candidate)
        : [candidate];
      for (const file of files) {
        if (!keep.has(file)) {
          keep.add(file);
          if (file.endsWith(".html")) queue.push(file);
        }
      }
    } catch {
      throw new Error(
        `Missing local report reference: ${path.relative(root, sourceFile)} -> ${match[1]}`,
      );
    }
  }
}

function sha256(buffer) {
  return createHash("sha256").update(buffer).digest("hex");
}

async function buildReportSubset(reportKey, rootPage) {
  const reportRoot = path.join(reportsRoot, reportKey);
  const allFiles = await walk(reportRoot);
  const keep = new Set();
  const queue = [];

  for (const name of ["index.html", "report_manifest.json"]) {
    const file = path.join(reportRoot, name);
    keep.add(file);
    if (name.endsWith(".html")) queue.push(file);
  }

  for (const file of allFiles) {
    if (path.dirname(file) === reportRoot && file.endsWith(".md")) keep.add(file);
  }

  const rootReferencePattern = new RegExp(
    `["'](/reports/${reportKey}/[^"']+)["']`,
    "g",
  );
  for (const match of rootPage.matchAll(rootReferencePattern)) {
    const candidate = candidateFromReference(
      reportKey,
      reportRoot,
      path.join(root, "app", "page.tsx"),
      match[1],
    );
    if (candidate) keep.add(candidate);
  }

  while (queue.length > 0) {
    const sourceFile = queue.shift();
    await collectReferences(reportKey, reportRoot, sourceFile, keep, queue);
  }

  const manifestPath = path.join(reportRoot, "hosted_subset_manifest.json");
  keep.delete(manifestPath);
  const removed = allFiles.filter((file) => !keep.has(file) && file !== manifestPath);
  const beforeBytes = (
    await Promise.all(allFiles.map(async (file) => (await stat(file)).size))
  ).reduce((sum, size) => sum + size, 0);
  const keptBytes = (
    await Promise.all([...keep].map(async (file) => (await stat(file)).size))
  ).reduce((sum, size) => sum + size, 0);

  if (apply) {
    await Promise.all(removed.map((file) => rm(file)));
    const entries = [];
    for (const file of [...keep].sort()) {
      const body = await readFile(file);
      entries.push({
        path: path.relative(reportRoot, file),
        bytes: body.length,
        sha256: sha256(body),
      });
    }
    await writeFile(
      manifestPath,
      `${JSON.stringify(
        {
          schema_version: "pearl.hosted_report_subset.v1",
          status: "pass_hosted_subset_manifest",
          source_manifest: "report_manifest.json",
          file_count: entries.length,
          files: entries,
        },
        null,
        2,
      )}\n`,
      "utf8",
    );
  }

  return {
    report: reportKey,
    before_files: allFiles.length,
    kept_files: keep.size + (apply ? 1 : 0),
    removed_files: removed.length,
    before_bytes: beforeBytes,
    kept_bytes: keptBytes,
  };
}

const rootPage = await readFile(path.join(root, "app", "page.tsx"), "utf8");
const results = [];
for (const reportKey of reportKeys) {
  results.push(await buildReportSubset(reportKey, rootPage));
}
console.log(JSON.stringify({ apply, reports: results }, null, 2));
