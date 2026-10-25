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
    "/auth/me": {username:"unit-user", roles:["customer"]},
    "/service/orders/O-2001": { order_id: "O-2001" },
    "/service/orders/O-2001/return-eligibility": { decision: "NO_REASON_ALLOWED" },
    "/service/orders/O-2001/return-drafts": draft,
    "/service/orders/O-2001/return-drafts/D-1/confirm": { status: "SUBMITTED" },
  };
  vm.runInNewContext(fs.readFileSync(require.resolve("./service.js"), "utf8"), {
    document: { getElementById: element, cookie:"opspilot_csrf=unit-csrf" },
    sessionStorage: { removeItem: () => {} },
    window: { addEventListener() {}, location:{replace(){}} },
    FormData: class FormData {},
    fetch: async (path, options) => {
      requests.push({ path, options });
      assert.ok(Object.hasOwn(responses, path), path);
      return { ok: true, json: async () => responses[path] };
    },
  });
  const tick = () => new Promise(resolve => setImmediate(resolve));
  await tick();
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
  assert.equal(confirmation.options.headers.Authorization, undefined);
  assert.equal(confirmation.options.headers["X-CSRF-Token"], "unit-csrf");
  assert.equal(confirmation.options.credentials, "same-origin");
  assert.equal(confirmation.options.method, "POST");
  assert.equal(JSON.parse(confirmation.options.body).product, draft.product);
  assert.equal(JSON.parse(confirmation.options.body).amount_cents, draft.amount_cents);
  assert.equal(JSON.parse(confirmation.options.body).expires_at, draft.expires_at);
  assert.equal(element("confirm-panel").hidden, true);
});

test("残留调试令牌不能登录，匿名会跳回登录页并且不发送业务请求",async()=>{
  const elements=new Map(), paths=[], redirects=[], removed=[];
  const element=id=>{
    if(!elements.has(id))elements.set(id,{
      value:"",checked:false,hidden:false,disabled:false,textContent:"",listeners:{},
      addEventListener(name,fn){this.listeners[name]=fn;}
    });
    return elements.get(id);
  };
  vm.runInNewContext(fs.readFileSync(require.resolve("./service.js"),"utf8"),{
    document:{getElementById:element,cookie:""},
    sessionStorage:{getItem:()=>"old-admin-token",removeItem:key=>removed.push(key)},
    window:{addEventListener(){},location:{replace:path=>redirects.push(path)}},
    FormData:class {},
    fetch:async(path)=>{paths.push(path);return{ok:false,status:401,json:async()=>({detail:"登录失效"})};}
  });
  const tick=()=>new Promise(resolve=>setImmediate(resolve));
  await tick();
  element("order-form").listeners.submit({preventDefault(){}});
  await tick();
  assert.deepEqual(paths,["/auth/me"]);
  assert.deepEqual(redirects,["/demo"]);
  assert.deepEqual(removed,["opspilot-access-token"]);
  assert.equal(element("service-main").hidden,true);
});

test("普通用户页没有调试令牌输入或开发调试链接",()=>{
  const service=fs.readFileSync(require.resolve("./service.html"),"utf8");
  const portal=fs.readFileSync(require.resolve("./portal.html"),"utf8");
  assert.equal(service.includes('id="access-token"'),false);
  assert.equal(portal.includes('href="/demo/legacy"'),false);
  assert.equal(portal.includes("开发调试入口"),false);
});
