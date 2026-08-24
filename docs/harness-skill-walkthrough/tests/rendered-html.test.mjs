import assert from "node:assert/strict";
import test from "node:test";

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);
  return worker.fetch(
    new Request("http://localhost/", { headers: { accept: "text/html" } }),
    { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
    { waitUntil() {}, passThroughOnException() {} },
  );
}

test("server-renders the Harness Skill walkthrough", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /<html lang="zh-CN">/i);
  assert.match(html, /Robot Harness · 三个 Skill Walkthrough/);
  assert.match(html, /从一句话到/);
  assert.match(html, /Interactive walkthrough/);
  assert.match(html, /text2env\.compile/);
  assert.match(html, /text2env\.replay/);
  assert.match(html, /text2env\.validate/);
  assert.match(html, /先复用，再 fallback/);
  assert.match(html, /static validation/);
  assert.match(html, /publishable/);
  assert.match(html, /Self-improving loop/);
  assert.doesNotMatch(html, /codex-preview|Your site is taking shape|Building your site/);
});
