const { test } = require("node:test");
const assert = require("node:assert/strict");
const { actionsFor, runPath, approvalText } = require("./demo.js");

test("只有等待确认并带具体快照时才出现执行按钮", () => {
  assert.equal(actionsFor({ status: "WAITING_CONFIRMATION" }).approve, false);
  assert.equal(actionsFor({ status: "WAITING_CONFIRMATION", action_id: "act-1" }).approve, true);
  assert.equal(actionsFor({ status: "STALE_CONFIRMATION", return_draft: {} }).approve, false);
  assert.equal(actionsFor({ status: "STALE_CONFIRMATION" }).refresh, true);
  assert.equal(actionsFor({ status: "WAITING_REASON" }).reason, true);
  assert.equal(actionsFor({ status: "COMPLETED", action_id: "act-1" }).approve, false);
});

test("恢复路径限定在当前任务编号", () => {
  assert.equal(runPath("thread 1", "/resume"), "/agent-graph/runs/thread%201/resume");
  assert.throws(() => runPath(" "), /任务编号/);
});

test("确认快照显示订单、金额和有效期", () => {
  const text = approvalText({
    thread_id: "thread-1",
    return_reason: "商品不适用",
    return_draft: {
      order_id: "O-2001", product: "机械键盘", amount_cents: 3555,
      draft_id: "draft-1", expires_at: "2026-09-14T11:00:00Z",
    },
  });
  for (const required of ["O-2001", "机械键盘", "¥35.55", "draft-1", "2026-09-14T11:00:00Z", "商品不适用"]) {
    assert.ok(text.includes(required));
  }
});
