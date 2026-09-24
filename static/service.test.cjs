const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

test("售后申请需勾选快照，并将显示的金额和有效期提交给服务端", async () => {
  const elements = new Map();
  const requests = [];
  const element = id => {
    if (!elements.has(id)) {
      elements.set(id, {
        value: "", checked: false, disabled: false, hidden: false,
        textContent: "", listeners: {},
        addEventListener(name, callback) { this.listeners[name] = callback; },
      });
    }
    return elements.get(id);
  };
  const draft = {
    draft_id: "D-1", order_id: "O-2001", product: "键盘", amount_cents: 2599,
    expires_at: "2026-09-23T10:00:00Z", status: "WAITING_CONFIRMATION",
  };
  const responses = {
    "/service/orders/O-2001": { order_id: "O-2001" },
    "/service/orders/O-2001/return-eligibility": { decision: "NO_REASON_ALLOWED" },
    "/service/orders/O-2001/return-drafts": draft,
    "/service/orders/O-2001/return-drafts/D-1/confirm": { status: "SUBMITTED" },
  };
  vm.runInNewContext(fs.readFileSync(require.resolve("./service.js"), "utf8"), {
    document: { getElementById: element },
    sessionStorage: { getItem: () => "token", setItem: () => {} },
    FormData: class FormData {},
    fetch: async (path, options) => {
      requests.push({ path, options });
      assert.ok(Object.hasOwn(responses, path), path);
      return { ok: true, json: async () => responses[path] };
    },
  });
  const tick = () => new Promise(resolve => setImmediate(resolve));
  element("order-id").value = "O-2001";
  element("order-form").listeners.submit({ preventDefault() {} });
  await tick();
  element("start-draft").listeners.click();
  await tick();
  assert.equal(element("confirm-draft").disabled, true);
  assert.match(element("draft-preview").textContent, /¥25\.99/);
  assert.equal(element("confirm-panel").hidden, false);
  element("confirm-check").checked = true;
  element("confirm-check").listeners.change();
  assert.equal(element("confirm-draft").disabled, false);
  element("confirm-draft").listeners.click();
  await tick();
  const confirmation = requests.find(item => item.path.endsWith("/confirm"));
  assert.ok(confirmation);
  assert.equal(confirmation.options.headers.Authorization, "Bearer token");
  assert.equal(confirmation.options.method, "POST");
  assert.equal(JSON.parse(confirmation.options.body).product, draft.product);
  assert.equal(JSON.parse(confirmation.options.body).amount_cents, draft.amount_cents);
  assert.equal(JSON.parse(confirmation.options.body).expires_at, draft.expires_at);
  assert.equal(element("confirm-panel").hidden, true);
});
