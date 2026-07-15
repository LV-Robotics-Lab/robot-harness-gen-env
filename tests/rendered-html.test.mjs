import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import { access, readFile, stat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const reports = ["sceneagent", "text2env", "openxsim", "harness", "openxsim-v1"];

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);

  return worker.fetch(
    new Request("http://localhost/", {
      headers: { accept: "text/html", host: "localhost" },
    }),
    {
      ASSETS: {
        fetch: async () => new Response("Not found", { status: 404 }),
      },
    },
    {
      waitUntil() {},
      passThroughOnException() {},
    },
  );
}

function sha256(filePath) {
  return new Promise((resolve, reject) => {
    const digest = createHash("sha256");
    const input = createReadStream(filePath);
    input.on("data", (chunk) => digest.update(chunk));
    input.on("end", () => resolve(digest.digest("hex")));
    input.on("error", reject);
  });
}

test("server-renders the finished PEARL evidence portal", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /<title>PEARL Self-Improving Agents<\/title>/i);
  assert.match(html, /Five reports\. One evidence chain\./);
  assert.match(html, /DECLARED V1 GATES PASS/);
  assert.match(html, /EXPANDED TODOS ACTIVE/);
  assert.doesNotMatch(html, /codex-preview|Your site is taking shape/);

  for (const report of reports) {
    assert.match(html, new RegExp(`/reports/${report}/index\\.html`));
    assert.match(
      html,
      new RegExp(`/reports/${report}/hosted_subset_manifest\\.json`),
    );
  }
});

test("bundles every hosted report asset with matching size and SHA-256", async () => {
  let verifiedFiles = 0;

  for (const report of reports) {
    const reportRoot = path.join(root, "public", "reports", report);
    const manifestPath = path.join(reportRoot, "hosted_subset_manifest.json");
    const manifest = JSON.parse(await readFile(manifestPath, "utf8"));

    assert.equal(manifest.file_count, manifest.files.length);
    assert.equal(manifest.status, "pass_hosted_subset_manifest");
    assert.equal(manifest.source_manifest, "report_manifest.json");
    await access(path.join(reportRoot, "index.html"));
    await access(path.join(reportRoot, "report_manifest.json"));

    for (const entry of manifest.files) {
      const filePath = path.join(reportRoot, entry.path);
      const fileStat = await stat(filePath);
      assert.equal(fileStat.size, entry.bytes, `${report}/${entry.path} size`);
      assert.equal(
        await sha256(filePath),
        entry.sha256,
        `${report}/${entry.path} SHA-256`,
      );
      verifiedFiles += 1;
    }
  }

  assert.ok(verifiedFiles >= 500, `expected at least 500 hosted report files, got ${verifiedFiles}`);
  await access(path.join(root, "public", "og.png"));
});
