"use strict";

function cookieValue(cookie, name) {
  const item = cookie.split(";").map(x => x.trim()).find(x => x.startsWith(name + "="));
  return item ? decodeURIComponent(item.slice(name.length + 1)) : "";
}
function turnActions(turn) {
  const result = turn.result || {};
  return {
    confirm: turn.kind === "agent" && turn.status === "WAITING_CONFIRMATION" && Boolean(result.return_draft || result.action_id),
    reason: turn.kind === "agent" && turn.status === "WAITING_REASON",
    refresh: turn.kind === "agent" && turn.status === "STALE_CONFIRMATION",
  };
}
function imagePath(conversationId, imageId) {
  return "/conversations/" + encodeURIComponent(conversationId) + "/images/" + encodeURIComponent(imageId);
}
function canSyncTurn(turn) {
  return ["RUNNING","RESULT_UNKNOWN"].includes(turn.status);
}
function shouldReuseImages(question, hasPrevious) {
  return hasPrevious && /这张图|上一张图|刚才的图|图片中|图里|图中/.test(question)
    && !/O-[0-9]{4}|申请退货|提交退货|办理退货/.test(question);
}
class ConversationDraft {
  constructor(makeId = () => crypto.randomUUID()) {
    this.makeId = makeId;
    this.version = 0;
    this.reset();
  }
  reset() {
    this.version += 1;
    this.id = this.key = this.inFlight = null;
  }
  ensure(create) {
    if (this.id) return Promise.resolve(this.id);
    if (this.inFlight) return this.inFlight;
    this.key ||= this.makeId();
    const version = this.version;
    const body = {title:"新会话", conversation_id:this.key};
    const flight = Promise.resolve().then(() => create(body)).then(result => {
      if (this.version !== version) throw new Error("原草稿已失效，请在当前会话重新操作");
      if (!result || typeof result.id !== "string") throw new Error("未取得有效会话编号，请重试原草稿");
      this.id = result.id;
      return this.id;
    }).finally(() => { if (this.inFlight === flight) this.inFlight = null; });
    this.inFlight = flight;
    return flight;
  }
}
if (typeof module !== "undefined") module.exports = { cookieValue, turnActions, imagePath, canSyncTurn, ConversationDraft, shouldReuseImages };

if (typeof document !== "undefined") {
  const el = id => document.getElementById(id);
  let conversationId = null, selectedImages = [], nextOffset = null, busy = false, pending = null;
  const conversationDraft = new ConversationDraft();
  let runtime = null, previousImages = [], pendingView = null;
  let olderBefore = null;
  const label = {RUNNING:"执行中", COMPLETED:"已完成", WAITING_REASON:"等待补充原因", WAITING_CONFIRMATION:"等待确认", STALE_CONFIRMATION:"确认已失效", RESULT_UNKNOWN:"结果待核实", FAILED:"本轮失败", CANCELLED:"已取消", EXPIRED:"已过期"};

  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  }
  function loggedOut() {
    conversationDraft.reset();
    runtime = null; previousImages = [];
    conversationId = null; selectedImages = []; pending = null;
    pendingView=null;
    olderBefore=null;el("older-messages").hidden=true;
    el("messages").replaceChildren(); el("attachments").replaceChildren();
    el("conversation-list").replaceChildren(); el("workspace").hidden = true;
    el("login-panel").hidden = false; el("logout").hidden = true;
    el("identity").textContent = "未登录";
    el("service-link").hidden = true;
    el("message").value = ""; el("image-files").value = "";
    el("password").value = ""; el("external-consent").checked = false;
    el("conversation-title").textContent = "新会话";
    el("messages").setAttribute("aria-busy", "false");
  }
  async function request(path, method = "GET", body, timeout = 180000, transportSignal) {
    const headers = {};
    const csrf = cookieValue(document.cookie, "opspilot_csrf");
    if (csrf && method !== "GET") headers["X-CSRF-Token"] = csrf;
    const options = { method, headers, credentials:"same-origin", cache:"no-store" };
    if (body instanceof FormData) options.body = body;
    else if (body !== undefined) { headers["Content-Type"] = "application/json"; options.body = JSON.stringify(body); }
    const controller = new AbortController(); options.signal = controller.signal;
    const abortTransport = () => controller.abort();
    transportSignal?.addEventListener("abort", abortTransport, {once:true});
    if (transportSignal?.aborted) controller.abort();
    const timer = setTimeout(() => controller.abort(), timeout);
    try {
      const response = await fetch(path, options);
      const data = await response.json();
      if (!response.ok) {
        if (response.status === 401) loggedOut();
        const error = new Error("HTTP " + response.status + "：" + (typeof data.detail === "string" ? data.detail : "请求未完成"));
        error.status = response.status;
        throw error;
      }
      return data;
    } finally {
      clearTimeout(timer);
      transportSignal?.removeEventListener("abort", abortTransport);
    }
  }
  async function perform(task) {
    if (busy) return;
    busy = true;
    document.querySelectorAll("button,input,textarea,select").forEach(node => { node.disabled = true; });
    el("notice").textContent = "正在处理…";
    try { await task(); el("notice").textContent = "操作完成"; }
    catch (error) {
      el("notice").textContent = error.message + "\n不会自动重发。可刷新会话，业务结果未知时先同步原任务。";
      el("login-notice").textContent = error.message;
    } finally {
      busy = false;
      document.querySelectorAll("button,input,textarea,select").forEach(node => { node.disabled = false; });
      document.querySelectorAll(".confirm-execution").forEach(node => { node.disabled = !node.approvalCheck.checked; });
    }
  }
  async function ensureConversation() {
    if (!conversationId) {
      conversationId = await conversationDraft.ensure(body => request("/conversations", "POST", body));
    }
    return conversationId;
  }
  function openDraft() {
    conversationDraft.reset();
    conversationId = null; selectedImages = []; pending = null;
    el("conversation-title").textContent = "新会话";
    previousImages = [];
    olderBefore=null;el("older-messages").hidden=true;
    pendingView=null;
    el("message").value = ""; el("image-files").value = "";
    el("external-consent").checked = false;
    el("messages").replaceChildren(element("p","发送第一条消息后才会出现在历史列表。","empty-state"));
    renderAttachments();
    document.querySelectorAll(".conversation-link").forEach(node => node.classList.remove("selected"));
  }
  async function listConversations(offset = 0, timeout = 180000) {
    const result = await request("/conversations?offset=" + offset,"GET",undefined,timeout);
    if (!offset) el("conversation-list").replaceChildren();
    result.items.forEach(item => {
      const button = element("button", item.title, "conversation-link" + (item.id === conversationId ? " selected" : ""));
      button.type = "button";
      button.addEventListener("click", () => perform(async () => {
        selectedImages = []; pending = null; renderAttachments(); await loadConversation(item.id);
      }));
      el("conversation-list").append(button);
    });
    nextOffset = result.next_offset; el("more-conversations").hidden = nextOffset === null;
  }
  function imagesFor(container, cid, ids) {
    const strip = element("div", undefined, "image-strip");
    ids.forEach(id => { const image = element("img"); image.src = imagePath(cid, id); image.alt = "本会话上传的图片"; image.loading = "lazy"; strip.append(image); });
    container.append(strip);
  }
  function renderAttachments() {
    el("attachments").replaceChildren();
    selectedImages.forEach(id => {
      const box = element("div", undefined, "attachment"); imagesFor(box, conversationId, [id]);
      const remove = element("button", "移除本轮选择"); remove.type = "button";
      remove.addEventListener("click", () => { selectedImages = selectedImages.filter(value => value !== id); pending = null; renderAttachments(); });
      box.append(remove); el("attachments").append(box);
    });
  }
  async function action(turn, body) {
    await request("/conversations/" + conversationId + "/turns/" + turn.id + "/actions", "POST", body);
    await loadConversation(conversationId);
  }
  function renderTurn(turn) {
    const box = element("article", undefined, "turn");
    box.append(element("div", turn.message, "user-message"));
    imagesFor(box, conversationId, turn.image_ids);
    const result = turn.result || {};
    box.append(element("div", (turn.mode === "mock" ? "历史模拟记录" : "售后助手") + " · " + (label[turn.status] || turn.status), "turn-meta"));
    box.append(element("div", result.answer || (turn.kind === "vision"
      ? "本轮没有取得识图结果，未执行业务操作。请核对真实模式配置或等待限流恢复。"
      : "尚无完整回答，请刷新或同步原任务。"), "assistant-message"));
    const actions = turnActions(turn);
    if (actions.confirm) {
      const approval = element("div", undefined, "approval");
      const draft = result.return_draft;
      const snapshot = draft ? "订单："+draft.order_id+"\n商品："+draft.product+"\n金额：¥"+(draft.amount_cents/100).toFixed(2)+"\n草稿："+draft.draft_id+"\n有效期："+draft.expires_at : "操作：" + result.action_id;
      approval.append(element("h3", "请核对本次业务操作"), element("pre", snapshot));
      const check = element("input"); check.type = "checkbox";
      const text = element("label", undefined, "check"); text.append(check, element("span", "我已核对以上快照，确认提交这一次申请"));
      const confirm = element("button", "确认提交", "confirm-execution"); confirm.type = "button"; confirm.disabled = true; confirm.approvalCheck = check;
      check.addEventListener("change", () => { confirm.disabled = !check.checked || busy; });
      confirm.addEventListener("click", () => { if (check.checked) perform(() => action(turn, {action:"confirm",expected_draft_id:draft?.draft_id || null})); });
      const cancel = element("button", "取消本次操作", "secondary"); cancel.type = "button";
      cancel.addEventListener("click", () => perform(() => action(turn, {action:"cancel"})));
      approval.append(text, confirm, cancel); box.append(approval);
    }
    if (actions.reason) {
      const form = element("form", undefined, "approval");
      const reason = element("textarea"); reason.required = true; reason.maxLength = 1000; reason.placeholder = "补充退货原因"; reason.setAttribute("aria-label", "退货原因");
      const category = element("select"); category.setAttribute("aria-label", "原因类型");
      [["OTHER","其他"],["QUALITY_ISSUE","质量问题"],["PERSONAL_PREFERENCE","个人偏好"]].forEach(([value,text]) => {const option=element("option",text);option.value=value;category.append(option);});
      const button=element("button","提交原因"); button.type="submit";
      form.append(reason,category,button); form.addEventListener("submit", event => {event.preventDefault();perform(() => action(turn,{action:"reason",return_reason:reason.value.trim(),reason_code:category.value}));});box.append(form);
    }
    if (actions.refresh) {const button=element("button","刷新确认快照");button.type="button";button.addEventListener("click",()=>perform(()=>action(turn,{action:"refresh"})));box.append(button);}
    if (canSyncTurn(turn)) {
      const sync=element("button","检查处理进度","secondary");sync.type="button";sync.addEventListener("click",()=>perform(async()=>{await request("/conversations/"+conversationId+"/turns/"+turn.id+"/sync","POST");await loadConversation(conversationId);}));box.append(sync);
    }
    if (turn.status === "FAILED") {
      const retry=element("button","将本题放回输入框（不自动发送）","secondary");retry.type="button";
      retry.addEventListener("click",()=>{selectedImages=turn.image_ids.slice();pending=null;el("message").value=turn.message;el("external-consent").checked=false;renderAttachments();el("message").focus();});box.append(retry);
    }
    return box;
  }
  async function loadConversation(id, timeout = 180000) {
    const data = await request("/conversations/" + id,"GET",undefined,timeout);
    conversationDraft.reset();
    previousImages = [...data.turns].reverse().find(turn => turn.image_ids?.length)?.image_ids || [];
    conversationId = data.id; el("conversation-title").textContent = data.title;
    olderBefore=data.next_before || null;el("older-messages").hidden=!olderBefore;
    el("messages").replaceChildren(...data.turns.map(renderTurn));
    pendingView=null;
    if (!data.turns.length) el("messages").append(element("p","可以询问订单、政策，或上传图片。","empty-state"));
    await listConversations(0,timeout);
  }
  function pendingMessage(message) {
    pendingView?.remove();
    const box = element("article", undefined, "turn pending-turn");
    pendingView=box;
    box.append(element("div",message,"user-message"));
    const status = element("div","正在发送问题…","assistant-message thinking-state");
    status.setAttribute("role","status"); status.setAttribute("aria-live","polite");
    box.append(status);
    el("messages").querySelector?.(".empty-state")?.remove();
    el("messages").append(box);
    el("messages").setAttribute("aria-busy","true");
    box.scrollIntoView?.({behavior:"smooth",block:"nearest"});
    return {box,status};
  }
  async function displayAccepted(turn, cid, message) {
    if (!runtime || conversationId!==cid) return;
    // A saved result remains authoritative even if the following history refresh fails.
    const box=renderTurn({message,image_ids:[],...turn});
    if (pendingView) { pendingView.replaceWith?.(box); }
    else el("messages").append(box);
    pendingView=null;
    try { await loadConversation(cid,8000); }
    catch (error) {
      if (error.status===401 || !runtime || conversationId!==cid) throw error;
      box.append(element("p","结果已读取，历史列表暂时未刷新；可稍后刷新会话。","note"));
    }
  }
  async function sendMessage(message, body) {
    // 先渲染本地消息；仅服务端状态能证明已接收，不能把网络发出当作业务成功。
    const view = pendingMessage(message);
    let stopped = false, timer = null, submission = null, cid = null;
    const transport = new AbortController();
    let acceptObserved, rejectObserved;
    const observed = new Promise((resolve,reject) => { acceptObserved=resolve; rejectObserved=reject; });
    function show(text, thinking = true) {
      if (!stopped) { view.status.textContent=text; view.status.className="assistant-message"+(thinking?" thinking-state":" error-state"); }
    }
    async function readAccepted() {
      const data = await request("/conversations/"+cid,"GET",undefined,8000);
      const found=data.turns.find(turn=>turn.request_id===submission.body.request_id);
      if (found) return found;
      try {
        const recovered=await request("/conversations/"+cid+"/requests/"+submission.body.request_id,"GET",undefined,8000);
        return recovered.request_id===submission.body.request_id ? recovered : undefined;
      } catch(error) { if(error.status===404)return undefined;throw error; }
    }
    async function poll() {
      if (stopped || !submission || pending!==submission || conversationId!==cid) return;
      try {
        const turn = await readAccepted();
        if (!stopped && pending===submission && conversationId===cid && turn) {
          if (el("message").value.trim()===message) el("message").value="";
          if (turn.status==="RUNNING") show("问题已接收，正在思考或查询订单与政策，请稍候…");
          else { acceptObserved(turn); return; }
        }
      } catch (error) {
        if (error.status===401) { rejectObserved(error); return; }
        show("正在等待回答；暂时无法读取进度，不会自动重发。",false);
      }
      if (!stopped) timer=setTimeout(poll,3000);
    }
    try {
      await ensureConversation(); cid=conversationId;
      const signature=cid+JSON.stringify(body);
      if (pending && pending.signature===signature) {
        // A previous delivery is uncertain: only inspect its result, never POST again.
        submission=pending;
        const previous=await readAccepted();
        if (!previous) throw new Error("暂未查到原请求，请稍后检查进度；不会重新发送。");
        await displayAccepted(previous,cid,message);
        if (!["RUNNING","RESULT_UNKNOWN"].includes(previous.status)) pending=null;
        return;
      }
      if (!pending || pending.signature!==signature) pending={signature,body:{...body,request_id:crypto.randomUUID()}};
      submission=pending;
      if (body.image_ids.length) imagesFor(view.box,cid,body.image_ids);
      show("问题已发送，正在等待模型处理；尚未确认服务端接收…");
      timer=setTimeout(poll,1500);
      let result;
      try {
        result=await Promise.race([
          request("/conversations/"+cid+"/turns","POST",submission.body,180000,transport.signal),
          observed
        ]);
      }
      catch (error) {
        if (error.status===401 || !runtime) throw error;
        // HTTP失败也可能已登记消息；只读取，不重放模型或业务工具。
        try { result=await readAccepted(); } catch (_) { /* 状态未知时保留原请求号。 */ }
        if (!result) throw error;
      }
      if (pending!==submission || conversationId!==cid) return;
      if (result.status==="FAILED") {
        pending=null; // 明确失败才允许用户下一次手动发送使用新请求号。
        await displayAccepted(result,cid,message);
        throw new Error("本轮处理失败，消息已保留。可以修改问题或手动重试；不会自动发送。");
      }
      if (["RUNNING","RESULT_UNKNOWN"].includes(result.status)) {
        await displayAccepted(result,cid,message);
        throw new Error("问题已接收，结果仍待确认。请检查处理进度，不要重新创建业务申请。");
      }
      pending=null; selectedImages=[]; renderAttachments();
      if (el("message").value.trim()===message) el("message").value="";
      el("external-consent").checked=false;
      await displayAccepted(result,cid,message);
    } catch (error) {
      if (!runtime || (cid && conversationId!==cid)) throw error;
      if (!el("message").value.trim()) el("message").value=message;
      show(submission && pending===submission
        ? "发送或处理状态待确认，原请求号已保留。可检查进度；不会自动重发。"
        : "本轮未完成，问题已保留，可稍后手动重试。",false);
      if (submission && pending===submission && pendingView===view.box) {
        const check=element("button","检查原请求进度（不重新发送）","secondary"); check.type="button";
        check.addEventListener("click",()=>perform(async()=>{
          if (conversationId!==cid || pending!==submission) return;
          const turn=await readAccepted();
          if (!turn) throw new Error("暂未查到原请求，请稍后再检查；不会重新发送。");
          if (conversationId!==cid || pending!==submission) return;
          await displayAccepted(turn,cid,message);
          if (!["RUNNING","RESULT_UNKNOWN"].includes(turn.status)) pending=null;
        }));
        view.box.append(check);
      }
      throw error;
    } finally {
      stopped=true; if (timer!==null) clearTimeout(timer);
      // Cancel only the browser transport, never the persisted workflow.
      transport.abort();
      el("messages").setAttribute("aria-busy","false");
    }
  }
  async function enter() {
    const user = await request("/auth/me");
    runtime = await request("/conversations/runtime");
    if (!["local","cloud"].includes(runtime.location)) throw new Error("模型服务配置未就绪，请稍后重试");
    el("vision-status").textContent = runtime.notice;
    el("external-consent-panel").hidden = !runtime.requires_image_consent;
    el("identity").textContent = user.username; el("login-panel").hidden = true;
    el("workspace").hidden = false; el("logout").hidden = false;
    el("service-link").hidden = false;
    sessionStorage.removeItem("opspilot-access-token");
    await listConversations();
  }
  el("login-form").addEventListener("submit",event=>{event.preventDefault();perform(async()=>{
    await request("/auth/login","POST",{username:el("username").value.trim(),password:el("password").value});
    el("password").value="";el("login-notice").textContent="";await enter();
  });});
  el("logout").addEventListener("click",()=>perform(async()=>{await request("/auth/logout","POST");sessionStorage.removeItem("opspilot-access-token");loggedOut();}));
  el("new-conversation").addEventListener("click",()=>perform(async()=>{openDraft();}));
  el("more-conversations").addEventListener("click",()=>perform(()=>listConversations(nextOffset)));
  el("reload").addEventListener("click",()=>perform(async()=>{if(conversationId) await loadConversation(conversationId);else await listConversations();}));
  el("older-messages").addEventListener("click",()=>perform(async()=>{
    if(!conversationId || !olderBefore)return;
    const cid=conversationId;
    const data=await request("/conversations/"+cid+"?before="+encodeURIComponent(olderBefore));
    if(conversationId!==cid)return;
    const existing=Array.from(el("messages").children);
    el("messages").replaceChildren(...data.turns.map(renderTurn),...existing);
    olderBefore=data.next_before || null;el("older-messages").hidden=!olderBefore;
  }));
  el("image-files").addEventListener("change",()=>perform(async()=>{
    const files=Array.from(el("image-files").files);
    if (!files.length) return;
    if(selectedImages.length+files.length>3)throw new Error("每轮最多3张图片");
    if(files.some(file=>file.size>4*1024*1024))throw new Error("每张图片不超过4 MB");
    await ensureConversation();
    for(const file of files){const data=new FormData();data.append("file",file);const image=await request("/conversations/"+conversationId+"/images","POST",data);selectedImages.push(image.id);renderAttachments();}
    el("image-files").value="";pending=null;
  }));
  el("composer").addEventListener("submit",event=>{event.preventDefault();perform(async()=>{
    const message = el("message").value.trim();
    if (!message) throw new Error("请先输入问题，不会创建空会话");
    if (message.length > 1000) throw new Error("问题不能超过1000字，不会创建会话");
    if (!runtime) throw new Error("模型服务配置未就绪，请刷新页面");
    const reuse = !selectedImages.length && shouldReuseImages(message,previousImages.length > 0);
    if (runtime.requires_image_consent && (selectedImages.length || reuse)
        && !el("external-consent").checked) throw new Error("请先确认图片外发授权，不会提交本轮请求");
    const body={message,mode:"live",image_ids:selectedImages.slice(),reuse_images:reuse,
                allow_external_images:runtime.requires_image_consent && el("external-consent").checked};
    await sendMessage(message,body);
  });});
  enter().catch(error=>{loggedOut();if(!error.message.startsWith("HTTP 401"))el("login-notice").textContent=error.message;});
}
