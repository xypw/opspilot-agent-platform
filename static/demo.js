"use strict";

function actionsFor(state) {
  return {
    reason: state?.status === "WAITING_REASON",
    approve: state?.status === "WAITING_CONFIRMATION" &&
      Boolean(state.return_draft || state.action_id),
    refresh: state?.status === "STALE_CONFIRMATION",
  };
}

function runPath(threadId, suffix = "") {
  if (!threadId || !threadId.trim()) throw new Error("请填写任务编号");
  return "/agent-graph/runs/" + encodeURIComponent(threadId) + suffix;
}

function approvalText(state) {
  const draft = state.return_draft;
  if (draft) {
    return [
      "任务：" + state.thread_id,
      "订单：" + draft.order_id,
      "商品：" + draft.product,
      "金额：¥" + (draft.amount_cents / 100).toFixed(2),
      "草稿：" + draft.draft_id,
      "有效期至：" + draft.expires_at,
      "退货原因：" + (state.return_reason || "七天内无理由"),
      "确认后操作：创建退货申请",
    ].join("\n");
  }
  const action = state.tool_result || {};
  return [
    "任务：" + state.thread_id,
    "操作编号：" + state.action_id,
    "工单：" + action.ticket_id,
    "优先级：" + action.previous_priority + " → " + action.new_priority,
  ].join("\n");
}

if (typeof module !== "undefined") {
  module.exports = { actionsFor, runPath, approvalText };
}

if (typeof document !== "undefined") {
  const el = id => document.getElementById(id);
  const statusNames = {
    RUNNING: "运行中", WAITING_REASON: "等待补充原因",
    WAITING_CONFIRMATION: "等待确认", STALE_CONFIRMATION: "确认已失效",
    COMPLETED: "已完成", CANCELLED: "已取消", EXPIRED: "申请已过期",
  };
  let current = null;
  let busy = false;

  function syncControls() {
    const actions = actionsFor(current);
    el("reason-panel").hidden = !actions.reason;
    el("approval-panel").hidden = !actions.approve;
    el("stale-panel").hidden = !actions.refresh;
    document.querySelectorAll("button, input, textarea, select").forEach(item => {
      item.disabled = busy;
    });
  }

  function display(state) {
    current = state;
    el("thread-id").value = state.thread_id;
    el("status").textContent = statusNames[state.status] || state.status;
    el("answer").textContent = state.answer;
    el("trace").textContent = JSON.stringify(state.tool_trace, null, 2);
    el("result").textContent = JSON.stringify(state, null, 2);
    el("approval-details").textContent = approvalText(state);
    syncControls();
  }

  async function request(path, method = "GET", body) {
    const options = { method, cache: "no-store" };
    if (body instanceof FormData) options.body = body;
    else if (body !== undefined) {
      options.headers = { "Content-Type": "application/json" };
      options.body = JSON.stringify(body);
    }
    const response = await fetch(path, options);
    const data = await response.json();
    if (!response.ok) {
      const detail = typeof data.detail === "string"
        ? data.detail : JSON.stringify(data.detail || data);
      throw new Error("HTTP " + response.status + "：" + detail);
    }
    return data;
  }

  async function perform(task, changesRun = true) {
    if (busy) return;
    busy = true;
    el("notice").textContent = "正在处理，请稍候…";
    syncControls();
    try {
      await task();
      el("notice").textContent = "请求完成";
    } catch (error) {
      // 响应丢失不代表写入失败。禁用旧快照，先读取后端状态。
      if (changesRun) {
        current = null;
        el("status").textContent = "需重新读取状态";
        el("answer").textContent = "请求结果不确定，请先按任务编号读取状态。";
      }
      el("notice").textContent = error.message + (changesRun
        ? "\n请先读取状态，页面不会自动重试写操作。" : "\n请检查资料或服务配置。");
    } finally {
      busy = false;
      syncControls();
    }
  }

  el("start-form").addEventListener("submit", event => {
    event.preventDefault();
    if (busy) return;
    const threadId = crypto.randomUUID();
    el("thread-id").value = threadId;
    current = null;
    el("answer").textContent = "正在创建任务…";
    perform(async () => display(await request("/agent-graph/runs", "POST", {
      thread_id: threadId, message: el("message").value.trim(), mode: el("mode").value,
    })));
  });
  el("restore-form").addEventListener("submit", event => {
    event.preventDefault();
    perform(async () => display(await request(runPath(el("thread-id").value.trim()))));
  });
  el("thread-id").addEventListener("input", () => {
    current = null;
    el("status").textContent = "请读取此任务";
    syncControls();
  });
  el("reason-form").addEventListener("submit", event => {
    event.preventDefault();
    if (!actionsFor(current).reason) return;
    const path = runPath(current.thread_id, "/return-reason");
    perform(async () => display(await request(path, "POST", {
      return_reason: el("reason").value.trim(),
      reason_code: el("reason-code").value,
    })));
  });
  for (const [id, approved] of [["approve", true], ["cancel", false]]) {
    el(id).addEventListener("click", () => {
      if (!actionsFor(current).approve) return;
      const path = runPath(current.thread_id, "/resume");
      perform(async () => display(await request(path, "POST", { approved })));
    });
  }
  el("refresh-draft").addEventListener("click", () => {
    if (!actionsFor(current).refresh) return;
    const path = runPath(current.thread_id, "/refresh-return-confirmation");
    perform(async () => display(await request(path, "POST")));
  });
  el("upload-form").addEventListener("submit", event => {
    event.preventDefault();
    const file = el("document-file").files[0];
    if (!file || file.size > 10 * 1024 * 1024) {
      el("notice").textContent = "请选择不超过 10 MB 的 PDF";
      return;
    }
    const form = new FormData();
    form.append("file", file);
    form.append("document_id", "doc-" + crypto.randomUUID());
    form.append("title", el("document-title").value.trim());
    perform(async () => {
      const data = await request("/documents/upload", "POST", form);
      el("answer").textContent = "资料已入库：" + data.title +
        "\n文档编号：" + data.document_id +
        "\n页数：" + data.page_count + "\n可以开始提问。";
    }, false);
  });
  syncControls();
}
