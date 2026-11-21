const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");
const vm = require("node:vm");

test("static demo isolates requester views and resets fictional tickets", async () => {
  let reset;
  const context = {
    Date, Event,
    document: {
      querySelector: () => ({ addEventListener: (_, callback) => { reset = callback; } }),
      dispatchEvent: () => {},
    },
  };
  vm.runInNewContext(fs.readFileSync("dist/pages-demo.js", "utf8"), context);
  const { request } = context.supportDeskDemo;
  assert.equal((await request("analyst", "/tickets", "GET")).length, 3);
  assert.equal((await request("maya", "/tickets", "GET")).length, 2);
  assert.equal((await request("noah", "/tickets", "GET")).length, 1);
  await assert.rejects(request("noah", "/tickets/1", "GET"), { status: 404 });
  await assert.rejects(request("maya", "/tickets/1", "PATCH", { status: "In progress" }), { status: 403 });
  await assert.rejects(request("analyst", "/tickets/1", "PATCH", { status: "Resolved" }), { status: 400 });
  const initial = await request("analyst", "/tickets/1", "GET");
  assert.equal(initial.sla[0].state, "Due soon");
  await request("analyst", "/tickets/1", "PATCH", {
    status: "In progress", note: "Private triage", reply: "Investigating",
  });
  const mine = await request("maya", "/tickets/1", "GET");
  assert.ok(mine.activity.some((event) => event.message === "Public reply: Investigating"));
  assert.ok(mine.activity.every((event) => !event.message.includes("Private triage")));
  await request("analyst", "/tickets/1", "PATCH", { status: "Resolved" });
  const reopened = await request("analyst", "/tickets/1", "PATCH", { status: "In progress" });
  assert.equal(reopened.sla[1].due_at, initial.sla[1].due_at);
  assert.equal(reopened.sla[1].completed, false);
  await request("maya", "/tickets", "POST", { title: "Extra", description: "Extra sample", priority: "Low" });
  assert.equal((await request("maya", "/tickets", "GET")).length, 3);
  reset();
  assert.equal((await request("analyst", "/tickets", "GET")).length, 3);
  assert.equal((await request("analyst", "/tickets/1", "GET")).status, "Open");
});

test("opening a different ticket clears both comment fields", () => {
  const { readFileSync } = require("node:fs");
  const vm = require("node:vm");
  const path = require("node:path");
  const form = {
    elements: {
      assignee: {},
      note: { value: "Previous internal draft" },
      reply: { value: "Previous public draft" },
      status: { replaceChildren() {} },
    },
  };
  const node = {
    addEventListener() {},
    replaceChildren() {},
  };
  const document = {
    querySelector: (selector) => selector === "#detail-form" ? Object.assign(form, node) : node,
    querySelectorAll: () => [],
    addEventListener() {},
    createElement: () => ({}),
  };
  const context = vm.createContext({
    document,
    fetch: () => new Promise(() => {}),
    setInterval() {},
  });
  vm.runInContext(readFileSync(path.join(__dirname, "dist/app.js"), "utf8"), context);
  vm.runInContext(`renderDetail({
    id: 2, title: "Folder access", requester: "Noah", priority: "Low",
    description: "Access request", assignee: "", status: "Open", sla: [], activity: [],
  })`, context);
  require("node:assert/strict").equal(form.elements.note.value, "");
  require("node:assert/strict").equal(form.elements.reply.value, "");
});
