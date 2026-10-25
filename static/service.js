"use strict";

const el = id => document.getElementById(id);
let currentOrder = null;
let currentDraft = null;
let eligibility = null;
let reviewAccepted = false;
let signedIn = false;
sessionStorage.removeItem("opspilot-access-token");

function requireLogin() {
  signedIn = false;
  currentOrder = currentDraft = eligibility = null;
  reviewAccepted = false;
  el("service-main").hidden = true;
  ["order-result", "knowledge-answer", "draft-preview", "draft-result"].forEach(id => {
    el(id).textContent = "";
  });
  window.location.replace("/demo");
}

async function request(path, method = "GET", body) {
  const headers = {};
  const csrf = typeof document.cookie === "string" ? document.cookie.split(";")
    .map(item => item.trim()).find(item => item.startsWith("opspilot_csrf="))?.slice(14) : "";
  if (csrf && method !== "GET") headers["X-CSRF-Token"] = decodeURIComponent(csrf);
  const options = { method, headers, cache: "no-store", credentials: "same-origin" };
  if (body instanceof FormData) options.body = body;
  else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  if (response.status === 401) {
    requireLogin();
    throw new Error("登录已失效，请重新登录");
  }
  const data = await response.json();
  if (!response.ok) throw new Error("HTTP " + response.status + "：" +
    (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail || data)));
  return data;
}

async function run(task) {
  el("notice").textContent = "正在处理…";
  try {
    if (!signedIn) throw new Error("请先登录后办理售后");
    await task(); el("notice").textContent = "操作完成";
  }
  catch (error) { el("notice").textContent = error.message; }
}

function draftPath(suffix = "") {
  return "/service/orders/" + encodeURIComponent(currentOrder) +
    "/return-drafts/" + encodeURIComponent(currentDraft.draft_id) + suffix;
}

function showDraft(draft) {
  currentDraft = draft;
  el("draft-panel").hidden = false;
  el("draft-preview").textContent = [
    "订单：" + draft.order_id, "商品：" + draft.product,
    "金额：¥" + (draft.amount_cents / 100).toFixed(2),
    "草稿：" + draft.draft_id, "有效期至：" + draft.expires_at,
    "状态：" + draft.status,
  ].join("\n");
  el("confirm-check").checked = false;
  el("confirm-draft").disabled = true;
  const active = !["EXPIRED", "CANCELLED", "SUBMITTED"].includes(draft.status);
  el("reason-form").hidden = !active || eligibility?.decision !== "REASON_REQUIRED" || reviewAccepted;
  el("confirm-panel").hidden = !active || !(
    eligibility?.decision === "NO_REASON_ALLOWED" || reviewAccepted);
  el("cancel-draft").hidden = !active;
  el("refresh-draft").hidden = true;
}

el("confirm-check").addEventListener("change", () => {
  el("confirm-draft").disabled = !el("confirm-check").checked;
});

el("upload-form").addEventListener("submit", event => {
  event.preventDefault();
  run(async () => {
    const data = new FormData();
    const file = el("document-file").files[0];
    data.append("file", file);
    data.append("title", el("document-title").value.trim());
    data.append("document_id", "doc-" + Date.now());
    const result = await request("/documents/upload", "POST", data);
    el("notice").textContent = "已上传：" + result.title;
  });
});

el("knowledge-form").addEventListener("submit", event => {
  event.preventDefault();
  run(async () => {
    const result = await request("/service/knowledge/answer", "POST", {
      query: el("question").value.trim(), limit: 3,
      review_mode: el("review-mode").value,
    });
    el("knowledge-answer").textContent = result.answer;
  });
});

el("order-form").addEventListener("submit", event => {
  event.preventDefault();
  run(async () => {
    currentOrder = el("order-id").value.trim();
    currentDraft = null;
    reviewAccepted = false;
    el("draft-panel").hidden = true;
    el("start-draft").hidden = true;
    const path = "/service/orders/" + encodeURIComponent(currentOrder);
    const order = await request(path);
    eligibility = await request(path + "/return-eligibility");
    el("order-result").textContent = JSON.stringify({ order, eligibility }, null, 2);
    el("start-draft").hidden = !["NO_REASON_ALLOWED", "REASON_REQUIRED"].includes(eligibility.decision);
  });
});

el("start-draft").addEventListener("click", () => run(async () => {
  const path = "/service/orders/" + encodeURIComponent(currentOrder) + "/return-drafts";
  showDraft(await request(path, "POST"));
}));

el("reason-form").addEventListener("submit", event => {
  event.preventDefault();
  run(async () => {
    const review = await request(draftPath("/reason"), "POST", {
      return_reason: el("return-reason").value.trim(), reason_code: el("reason-code").value,
    });
    el("draft-result").textContent = JSON.stringify(review, null, 2);
    reviewAccepted = review.decision === "ACCEPTABLE";
    showDraft(await request(draftPath()));
    if (!reviewAccepted) el("reason-form").hidden = true;
  });
});

el("confirm-draft").addEventListener("click", () => run(async () => {
  if (!el("confirm-check").checked || !currentDraft) return;
  let result;
  try {
    result = await request(draftPath("/confirm"), "POST", {
      product: currentDraft.product, amount_cents: currentDraft.amount_cents,
      expires_at: currentDraft.expires_at,
    });
  } catch (error) {
    if (error.message.startsWith("HTTP 409")) el("refresh-draft").hidden = false;
    throw error;
  }
  el("draft-result").textContent = JSON.stringify(result, null, 2);
  el("confirm-panel").hidden = true;
  el("reason-form").hidden = true;
  el("cancel-draft").hidden = true;
}));

el("cancel-draft").addEventListener("click", () => run(async () => {
  showDraft(await request(draftPath("/cancel"), "POST"));
  el("reason-form").hidden = true;
  el("confirm-panel").hidden = true;
}));

el("refresh-draft").addEventListener("click", () => run(async () => {
  showDraft(await request(draftPath("/refresh"), "POST"));
}));

async function verifyServiceSession() {
  signedIn = false;
  el("service-main").hidden = true;
  try {
    const user = await request("/auth/me");
    signedIn = true;
    el("service-identity").textContent = "当前账号：" + user.username;
    el("upload-form").hidden = !user.roles.includes("knowledge_admin");
    el("service-main").hidden = false;
  } catch (error) {
    // 配置或网络异常也不展示操作区，不能降级为令牌或匿名模式。
    el("notice").textContent = error.message;
  }
}
window.addEventListener("pageshow", verifyServiceSession);
verifyServiceSession();
