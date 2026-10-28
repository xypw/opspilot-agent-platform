const {test}=require("node:test");
const assert=require("node:assert/strict");
const fs=require("node:fs");
const vm=require("node:vm");
const {cookieValue,turnActions,imagePath,canSyncTurn,ConversationDraft,shouldReuseImages}=require("./portal.js");

test("同一草稿并发创建只发一次，网络失败后的重试沿用原ID",async()=>{
  let resolve, calls=0;
  const draft=new ConversationDraft(()=>"stable-key");
  const create=body=>{calls++;assert.equal(body.conversation_id,"stable-key");return new Promise(done=>{resolve=done;});};
  const a=draft.ensure(create),b=draft.ensure(create);
  assert.equal(a,b);
  await Promise.resolve();
  assert.equal(calls,1);
  resolve({id:"created-id"});
  assert.equal(await a,"created-id");
  assert.equal(await draft.ensure(create),"created-id");
  assert.equal(calls,1);
  draft.reset();
  let first=true;
  const seen=[];
  const retry=async body=>{seen.push(body.conversation_id);if(first){first=false;throw new Error("response lost");}return{id:"same-row"};};
  await assert.rejects(draft.ensure(retry),/response lost/);
  assert.equal(await draft.ensure(retry),"same-row");
  assert.deepEqual(seen,["stable-key","stable-key"]);
});

test("重置或退出后不接纳旧草稿的迟到创建响应",async()=>{
  let finish;
  const draft=new ConversationDraft(()=>"old-key");
  const pending=draft.ensure(()=>new Promise(resolve=>{finish=resolve;}));
  await Promise.resolve();
  draft.reset();
  finish({id:"old-conversation"});
  await assert.rejects(pending,/草稿已失效/);
  assert.equal(draft.id,null);
});

function portalHarness(responder, allControls=false) {
  const nodes=new Map(), requests=[];
  let sequence=0;
  const timers=new Map(); let timerId=0;
  const node=()=>({
    value:"",checked:false,hidden:false,disabled:false,textContent:"",files:[],children:[],listeners:{},
    classList:{remove(){}},
    addEventListener(name,fn){this.listeners[name]=fn;},
    append(...items){this.children.push(...items);},
    replaceChildren(...items){this.children=items;},
    attributes:{},setAttribute(key,value){this.attributes[key]=value;},focus(){},
    remove(){this.removed=true;},replaceWith(other){this.replacement=other;},scrollIntoView(){}
  });
  const el=id=>{if(!nodes.has(id))nodes.set(id,node());return nodes.get(id);};
  vm.runInNewContext(fs.readFileSync(require.resolve("./portal.js"),"utf8"),{
    document:{getElementById:el,createElement:node,cookie:"opspilot_csrf=test-csrf",
      querySelectorAll:selector=>allControls && selector==="button,input,textarea,select"?[...nodes.values()]:[]},
    window:{confirm:()=>false},
    sessionStorage:{removeItem(){}},FormData:class{},crypto:{randomUUID:()=>"unit-request-"+(++sequence)},
    AbortController,
    setTimeout(fn,delay){const id=++timerId;timers.set(id,{fn,delay});return id;},
    clearTimeout(id){timers.delete(id);},
    fetch:async(path,options)=>{
      requests.push({path,options});
      if(path==="/conversations/runtime")return {ok:true,status:200,json:async()=>({
        location:"local",requires_image_consent:false,vision_available:true,notice:"本机模型"
      })};
      if(responder)return responder(path,options);
      const body=path==="/auth/me"?{username:"test-user",roles:["customer"]}:{items:[],next_offset:null};
      return {ok:true,status:200,json:async()=>body};
    }
  });
  return {el,requests,timers,async fire(delay){const item=[...timers].find(([,v])=>v.delay===delay);
    assert.ok(item,"expected timer "+delay);timers.delete(item[0]);await item[1].fn();}};
}

test("连续点击新会话不请求创建API，空白提交和取消选图也不创建",async()=>{
  const {el,requests}=portalHarness();
  const tick=()=>new Promise(resolve=>setImmediate(resolve));
  await tick();
  for(let i=0;i<20;i++){el("new-conversation").listeners.click();await tick();}
  el("message").value="   ";
  el("composer").listeners.submit({preventDefault(){}});
  await tick();
  el("image-files").listeners.change();
  await tick();
  assert.equal(requests.some(item=>item.options.method==="POST"),false);
  assert.equal(el("conversation-title").textContent,"新会话");
  assert.equal(el("messages").children.length,1);
});

test("创建已落库但响应丢失，页面重试不新增会话并只提交一次首条消息",async()=>{
  const rows=new Map(),createIds=[],turns=[];
  let drop=true;
  const reply=data=>({ok:true,status:200,json:async()=>data});
  const {el}=portalHarness(async(path,options)=>{
    if(path==="/auth/me")return reply({username:"user",roles:["customer"]});
    if(path.startsWith("/conversations?"))return reply({items:turns.length?[...rows.values()]:[],next_offset:null});
    if(path==="/conversations"){
      const body=JSON.parse(options.body);createIds.push(body.conversation_id);
      rows.set(body.conversation_id,{id:body.conversation_id,title:"test"});
      if(drop){drop=false;throw new Error("response lost");}
      return reply(rows.get(body.conversation_id));
    }
    if(path.endsWith("/turns")){
      turns.push(JSON.parse(options.body));
      return reply({status:"COMPLETED"});
    }
    return reply({id:[...rows.keys()][0],title:"test",turns:[]});
  });
  const tick=()=>new Promise(resolve=>setImmediate(resolve));
  await tick();
  el("message").value="查询订单 O-7101";el("mode").value="mock";
  el("composer").listeners.submit({preventDefault(){}});
  await tick();
  assert.match(el("notice").textContent,/response lost/);
  assert.equal(turns.length,0);
  el("composer").listeners.submit({preventDefault(){}});
  await tick();
  assert.equal(rows.size,1);
  assert.equal(createIds.length,2);
  assert.equal(createIds[0],createIds[1]);
  assert.equal(turns.length,1);
});

test("执行中的图片任务提供同步入口，但没有业务确认权限",()=>{
  assert.equal(canSyncTurn({kind:"vision",status:"RUNNING"}),true);
  assert.equal(canSyncTurn({kind:"vision",status:"COMPLETED"}),false);
  assert.equal(canSyncTurn({kind:"agent",status:"WAITING_CONFIRMATION"}),false);
  assert.equal(turnActions({kind:"vision",status:"RUNNING"}).confirm,false);
});

test("慢请求立即显示问题；只读轮询确认接收并显示处理中，不重复发送",async()=>{
  let finish,posted=0,requestId;
  const reply=data=>({ok:true,status:200,json:async()=>data});
  const running=()=>({id:"turn-1",request_id:requestId,message:"查询订单 O-7101",mode:"live",
    image_ids:[],kind:"agent",status:"RUNNING",result:null});
  const h=portalHarness(async(path,options)=>{
    if(path==="/auth/me")return reply({username:"user"});
    if(path.startsWith("/conversations?"))return reply({items:[],next_offset:null});
    if(path==="/conversations")return reply({id:"conv-1"});
    if(path.endsWith("/turns")){
      posted++;requestId=JSON.parse(options.body).request_id;
      return new Promise(resolve=>{finish=()=>resolve(reply({...running(),status:"COMPLETED"}));});
    }
    return reply({id:"conv-1",title:"test",turns:[running()]});
  });
  const tick=()=>new Promise(resolve=>setImmediate(resolve));
  await tick();h.el("message").value="查询订单 O-7101";
  h.el("composer").listeners.submit({preventDefault(){}});
  const view=h.el("messages").children.at(-1);
  assert.equal(view.children[0].textContent,"查询订单 O-7101");
  assert.match(view.children[1].textContent,/正在发送/);
  await tick();
  h.el("composer").listeners.submit({preventDefault(){}});
  assert.equal(posted,1);
  await h.fire(1500);
  assert.match(view.children[1].textContent,/问题已接收.*思考/);
  assert.equal(h.el("messages").attributes["aria-busy"],"true");
  await h.fire(3000);assert.equal(posted,1);
  finish();await tick();
  assert.equal(h.el("messages").attributes["aria-busy"],"false");
  assert.equal(h.timers.size,0);
});

test("思考时允许编辑下一句，禁止并发发送，迟到结果保留草稿且切换可取消",async()=>{
  let finish,posted=0;
  const reply=data=>({ok:true,status:200,json:async()=>data});
  const h=portalHarness(async(path,options)=>{
    if(path==="/auth/me")return reply({username:"user",roles:["customer"]});
    if(path.startsWith("/conversations?"))return reply({items:[],next_offset:null});
    if(path==="/conversations")return reply({id:"conv-1"});
    if(path.endsWith("/turns")){
      posted++;return new Promise(resolve=>{finish=()=>resolve(reply({status:"COMPLETED"}));});
    }
    return reply({id:"conv-1",title:"kept-title",turns:[]});
  },true);
  const tick=()=>new Promise(resolve=>setImmediate(resolve));await tick();
  h.el("message").value="first";
  h.el("composer").listeners.submit({preventDefault(){}});await tick();
  assert.equal(h.el("message").disabled,false);
  assert.equal(h.el("message").value,"");
  assert.equal(h.el("new-conversation").disabled,true);
  h.el("message").value="next question";
  h.el("composer").listeners.submit({preventDefault(){}});await tick();
  assert.equal(posted,1);
  finish();await tick();
  assert.equal(h.el("message").value,"next question");
  h.el("new-conversation").listeners.click();await tick();
  assert.equal(h.el("message").value,"next question");
  assert.equal(h.el("conversation-title").textContent,"kept-title");
});

test("自助注册成功自动登录，注册请求不携带角色或用户ID",async()=>{
  const reply=data=>({ok:true,status:200,json:async()=>data});
  const h=portalHarness(async path=>{
    if(path==='/auth/register')return reply({demo_order_id:'O-8000'});
    if(path==='/auth/me')return reply({username:'newuser',roles:['customer'],demo_order_id:'O-8000'});
    return reply({items:[],next_offset:null});
  });
  const tick=()=>new Promise(resolve=>setImmediate(resolve));await tick();
  h.el('register-mode').listeners.click();
  h.el('username').value='newuser';h.el('password').value='long-unit-password';
  h.el('login-form').listeners.submit({preventDefault(){}});await tick();
  const call=h.requests.find(x=>x.path==='/auth/register');
  assert.deepEqual(JSON.parse(call.options.body),{username:'newuser',password:'long-unit-password'});
  assert.equal(h.el('password').value,'');assert.equal(h.el('admin-link').hidden,true);
  assert.match(h.el('notice').textContent,/注册成功.*O-8000/);
});

test("明确失败后手动重试使用新请求号，不永远复用旧失败结果",async()=>{
  const ids=[];let turn;
  const reply=data=>({ok:true,status:200,json:async()=>data});
  const h=portalHarness(async(path,options)=>{
    if(path==="/auth/me")return reply({username:"user"});
    if(path.startsWith("/conversations?"))return reply({items:[],next_offset:null});
    if(path==="/conversations")return reply({id:"conv-1"});
    if(path.endsWith("/turns")){
      const body=JSON.parse(options.body);ids.push(body.request_id);
      turn={id:"turn-1",request_id:body.request_id,message:body.message,image_ids:[],kind:"agent",mode:"live",
        status:ids.length===1?"FAILED":"COMPLETED",result:{answer:"result"}};
      return ids.length===1?{ok:false,status:503,json:async()=>({detail:"temporary failure"})}:reply(turn);
    }
    return reply({id:"conv-1",title:"test",turns:turn?[turn]:[]});
  });
  const tick=()=>new Promise(resolve=>setImmediate(resolve));
  await tick();h.el("message").value="查询订单 O-7101";
  h.el("composer").listeners.submit({preventDefault(){}});await tick();
  assert.match(h.el("notice").textContent,/处理失败/);
  assert.equal(ids.length,1);
  h.el("composer").listeners.submit({preventDefault(){}});await tick();
  assert.equal(ids.length,2);assert.notEqual(ids[0],ids[1]);
  assert.equal(h.timers.size,0);
});

test("响应丢失且结果未知时，下一次核对仍沿用原请求号",async()=>{
  const ids=[];let turn;
  const reply=data=>({ok:true,status:200,json:async()=>data});
  const h=portalHarness(async(path,options)=>{
    if(path==="/auth/me")return reply({username:"user"});
    if(path.startsWith("/conversations?"))return reply({items:[],next_offset:null});
    if(path==="/conversations")return reply({id:"conv-1"});
    if(path.endsWith("/turns")){
      const body=JSON.parse(options.body);ids.push(body.request_id);
      turn={id:"turn-1",request_id:body.request_id,message:body.message,image_ids:[],kind:"agent",mode:"live",
        status:ids.length===1?"RESULT_UNKNOWN":"COMPLETED",result:{answer:"result"}};
      if(ids.length===1)throw new Error("response lost");
      return reply(turn);
    }
    return reply({id:"conv-1",title:"test",turns:turn?[turn]:[]});
  });
  const tick=()=>new Promise(resolve=>setImmediate(resolve));
  await tick();h.el("message").value="查询订单 O-7101";
  h.el("composer").listeners.submit({preventDefault(){}});await tick();
  assert.equal(ids.length,1);assert.match(h.el("notice").textContent,/结果仍待确认/);
  h.el("composer").listeners.submit({preventDefault(){}});await tick();
  assert.equal(ids.length,1); // recovery is GET-only, including an explicit second click
});

test("等待超时停止占用界面，但不假装取消服务端任务或自动重发",async()=>{
  let posted=0;
  const reply=data=>({ok:true,status:200,json:async()=>data});
  const h=portalHarness(async(path,options)=>{
    if(path==="/auth/me")return reply({username:"user"});
    if(path.startsWith("/conversations?"))return reply({items:[],next_offset:null});
    if(path==="/conversations")return reply({id:"conv-1"});
    if(path.endsWith("/turns")){
      posted++;return new Promise((_,reject)=>options.signal.addEventListener("abort",()=>reject(new Error("timeout"))));
    }
    return reply({id:"conv-1",title:"test",turns:[]});
  });
  const tick=()=>new Promise(resolve=>setImmediate(resolve));
  await tick();h.el("message").value="查询订单 O-7101";
  h.el("composer").listeners.submit({preventDefault(){}});await tick();
  await h.fire(180000);await tick();
  assert.equal(posted,1);
  assert.equal(h.el("messages").attributes["aria-busy"],"false");
  assert.match(h.el("messages").children.at(-1).children[1].textContent,/状态待确认/);
  assert.equal(h.timers.size,0);
});

test("Cookie只取指定CSRF字段，不读取HttpOnly会话",()=>{
  assert.equal(cookieValue("other=x; opspilot_csrf=unit-token", "opspilot_csrf"),"unit-token");
  assert.equal(cookieValue("other=x","opspilot_csrf"),"");
});

test("退出账号清除未发送文本、文件选择和外发授权，避免下个账号看到残留",async()=>{
  const h=portalHarness();
  const tick=()=>new Promise(resolve=>setImmediate(resolve));
  await tick();
  h.el("message").value="上一个用户未发送的私密问题";
  h.el("image-files").value="private-photo.png";
  h.el("password").value="cached-password";
  h.el("external-consent").checked=true;
  h.el("conversation-title").textContent="上一个用户的订单";
  h.el("logout").listeners.click();await tick();
  assert.equal(h.el("message").value,"");
  assert.equal(h.el("image-files").value,"");
  assert.equal(h.el("password").value,"");
  assert.equal(h.el("external-consent").checked,false);
  assert.equal(h.el("conversation-title").textContent,"新会话");
  assert.equal(h.el("workspace").hidden,true);
  assert.equal(h.requests.some(r=>r.path.endsWith("/turns")),false);
});

test("历史按游标加载更早页，不重发消息",async()=>{
  const reply=data=>({ok:true,status:200,json:async()=>data});let posted=0;
  const row=i=>({id:'t-'+i,message:'问题'+i,image_ids:[],kind:'agent',mode:'live',status:'COMPLETED',result:{answer:'答复'+i}});
  const h=portalHarness(async(path,options)=>{
    if(path==='/auth/me')return reply({username:'user'});
    if(path.startsWith('/conversations?'))return reply({items:[],next_offset:null});
    if(path==='/conversations')return reply({id:'paged'});
    if(path.endsWith('/turns')){posted++;return reply(row(136));}
    if(path.includes('?before='))return reply({id:'paged',title:'long',turns:Array.from({length:50},(_,i)=>row(i+37)),next_before:'t-37'});
    return reply({id:'paged',title:'long',turns:Array.from({length:50},(_,i)=>row(i+87)),next_before:'t-87'});
  });
  const tick=()=>new Promise(resolve=>setImmediate(resolve));await tick();h.el('message').value='问题136';
  h.el('composer').listeners.submit({preventDefault(){}});await tick();
  assert.equal(h.el('older-messages').hidden,false);assert.equal(h.el('messages').children.length,50);
  h.el('older-messages').listeners.click();await tick();
  assert.equal(h.el('messages').children.length,100);assert.equal(posted,1);
  assert.equal(h.el('messages').children[0].children[0].textContent,'问题37');
});

for (const status of ["COMPLETED","WAITING_CONFIRMATION","FAILED","RESULT_UNKNOWN"]) {
  test("原POST不返回，GET恢复真实状态 "+status+" 且不重复提交",async()=>{
    let requestId, posted=0;
    const reply=data=>({ok:true,status:200,json:async()=>data});
    const turn=()=>({id:"recovery-turn",request_id:requestId,message:"申请退货 O-7101",image_ids:[],
      kind:"agent",mode:"live",status,result:{answer:"已保存的结果",return_draft:{
        order_id:"O-7101",product:"虚构商品",amount_cents:100,draft_id:"synthetic-draft",expires_at:"2099-01-01"}}});
    const h=portalHarness(async(path,options)=>{
      if(path==="/auth/me")return reply({username:"user"});
      if(path.startsWith("/conversations?"))return reply({items:[],next_offset:null});
      if(path==="/conversations")return reply({id:"conv-recovery"});
      if(path.endsWith("/turns")){
        posted++;requestId=JSON.parse(options.body).request_id;
        return new Promise((_,reject)=>options.signal.addEventListener("abort",()=>reject(new Error("transport closed"))));
      }
      return reply({id:"conv-recovery",title:"test",turns:[turn()]});
    });
    const tick=()=>new Promise(resolve=>setImmediate(resolve));
    await tick();h.el("message").value="申请退货 O-7101";
    h.el("composer").listeners.submit({preventDefault(){}});await tick();
    await h.fire(1500);await tick();
    assert.equal(posted,1);assert.equal(h.timers.size,0);
    assert.equal(h.el("messages").attributes["aria-busy"],"false");
    const box=h.el("messages").children[0];
    assert.equal(box.children[3].textContent,"已保存的结果");
    if(status==="WAITING_CONFIRMATION"){
      const approval=box.children.find(n=>n.className==="approval");
      assert.ok(approval);const confirm=approval.children.find(n=>n.className==="confirm-execution");
      assert.equal(confirm.disabled,true);assert.equal(confirm.approvalCheck.checked,false);
    }
    assert.equal(h.requests.some(x=>x.path.endsWith("/actions")),false);
  });
}

test("发送断网后提供真实只读恢复按钮",async()=>{
  let online=false,requestId,posted=0;
  const reply=data=>({ok:true,status:200,json:async()=>data});
  const h=portalHarness(async(path,options)=>{
    if(path==="/auth/me")return reply({username:"user"});
    if(path.startsWith("/conversations?"))return reply({items:[],next_offset:null});
    if(path==="/conversations")return reply({id:"offline-conv"});
    if(path.endsWith("/turns")){posted++;requestId=JSON.parse(options.body).request_id;throw new Error("offline");}
    if(!online)throw new Error("offline");
    return reply({id:"offline-conv",title:"test",turns:[{id:"saved",request_id:requestId,
      message:"问题",image_ids:[],kind:"agent",mode:"live",status:"COMPLETED",result:{answer:"恢复结果"}}]});
  });
  const tick=()=>new Promise(resolve=>setImmediate(resolve));
  await tick();h.el("message").value="问题";
  h.el("composer").listeners.submit({preventDefault(){}});await tick();
  const button=h.el("messages").children.at(-1).children.at(-1);
  assert.match(button.textContent,/检查原请求进度/);
  online=true;button.listeners.click();await tick();
  assert.equal(h.el("messages").children[0].children[3].textContent,"恢复结果");
  assert.equal(posted,1);assert.equal(h.timers.size,0);
});

test("已收到结果而历史刷新断网时，不丢失快照或误报未发送",async()=>{
  const reply=data=>({ok:true,status:200,json:async()=>data});
  const h=portalHarness(async(path,options)=>{
    if(path==="/auth/me")return reply({username:"user"});
    if(path.startsWith("/conversations?"))return reply({items:[],next_offset:null});
    if(path==="/conversations")return reply({id:"saved-conv"});
    if(path.endsWith("/turns"))return reply({id:"saved-turn",request_id:JSON.parse(options.body).request_id,
      status:"WAITING_CONFIRMATION",kind:"agent",image_ids:[],result:{answer:"请确认草稿",return_draft:{
        order_id:"O-7101",product:"虚构商品",amount_cents:100,draft_id:"draft",expires_at:"2099-01-01"}}});
    throw new Error("history offline");
  });
  const tick=()=>new Promise(resolve=>setImmediate(resolve));
  await tick();h.el("message").value="申请退货";
  h.el("composer").listeners.submit({preventDefault(){}});await tick();
  const box=h.el("messages").children.at(-1).replacement;
  assert.ok(box);assert.equal(box.children[3].textContent,"请确认草稿");
  assert.match(box.children.at(-1).textContent,/结果已读取/);
  assert.equal(h.el("message").value,"");
  assert.equal(h.el("notice").textContent,"操作完成");assert.equal(h.timers.size,0);
});

test("轮询遇到登录过期停止等待并清除旧会话",async()=>{
  const reply=data=>({ok:true,status:200,json:async()=>data});
  const h=portalHarness(async(path,options)=>{
    if(path==="/auth/me")return reply({username:"user"});
    if(path.startsWith("/conversations?"))return reply({items:[],next_offset:null});
    if(path==="/conversations")return reply({id:"expired-conv"});
    if(path.endsWith("/turns"))return new Promise((_,reject)=>options.signal.addEventListener("abort",()=>reject(new Error("closed"))));
    return {ok:false,status:401,json:async()=>({detail:"expired"})};
  });
  const tick=()=>new Promise(resolve=>setImmediate(resolve));
  await tick();h.el("message").value="问题";
  h.el("composer").listeners.submit({preventDefault(){}});await tick();
  await h.fire(1500);await tick();
  assert.equal(h.el("workspace").hidden,true);assert.equal(h.el("messages").children.length,0);
  assert.equal(h.timers.size,0);
});

test("终态后的历史刷新悬挂有短超时，不再等待180秒",async()=>{
  const reply=data=>({ok:true,status:200,json:async()=>data});
  const h=portalHarness(async(path,options)=>{
    if(path==="/auth/me")return reply({username:"user"});
    if(path.startsWith("/conversations?"))return reply({items:[],next_offset:null});
    if(path==="/conversations")return reply({id:"saved-conv"});
    if(path.endsWith("/turns"))return reply({id:"saved-turn",status:"COMPLETED",kind:"agent",
      image_ids:[],result:{answer:"已完成结果"}});
    return new Promise((_,reject)=>options.signal.addEventListener("abort",()=>reject(new Error("history timeout"))));
  });
  const tick=()=>new Promise(resolve=>setImmediate(resolve));
  await tick();h.el("message").value="问题";
  h.el("composer").listeners.submit({preventDefault(){}});await tick();
  const box=h.el("messages").children.at(-1).replacement;
  assert.equal(box.children[3].textContent,"已完成结果");
  await h.fire(8000);await tick();
  assert.equal(h.el("messages").attributes["aria-busy"],"false");assert.equal(h.timers.size,0);
  assert.match(box.children.at(-1).textContent,/结果已读取/);
});

test("普通用户不选择模型或检索实现，也不展示内部工具JSON",()=>{
  const html=fs.readFileSync(require.resolve("./portal.html"),"utf8");
  const script=fs.readFileSync(require.resolve("./portal.js"),"utf8");
  for(const id of ["mode","use-policy","policy-query","reuse-images"]){
    assert.equal(html.includes('id="'+id+'"'),false);
  }
  assert.equal(script.includes("工具轨迹与验证数据"),false);
  assert.match(script,/mode:"live"/);
});

test("图片明确指代才沿用，不能把订单申请误变成图片任务",()=>{
  assert.equal(shouldReuseImages("这张图有破损吗",true),true);
  assert.equal(shouldReuseImages("这张图有破损吗",false),false);
  assert.equal(shouldReuseImages("查询订单 O-7101，这张图",true),false);
  assert.equal(shouldReuseImages("这张图帮我申请退货",true),false);
});
test("图片问答没有业务确认按钮，只有待确认Agent任务可以确认",()=>{
  assert.equal(turnActions({kind:"vision",status:"WAITING_CONFIRMATION",result:{return_draft:{}}}).confirm,false);
  assert.equal(turnActions({kind:"agent",status:"COMPLETED",result:{return_draft:{}}}).confirm,false);
  assert.equal(turnActions({kind:"agent",status:"WAITING_CONFIRMATION",result:{return_draft:{}}}).confirm,true);
});
test("图片URL限定为受保护的当前会话路径",()=>{
  assert.equal(imagePath("conv a","image b"),"/conversations/conv%20a/images/image%20b");
});
test("界面不使用innerHTML插入模型内容，并且保留显式确认与外发授权",()=>{
  const source=fs.readFileSync(require.resolve("./portal.js"),"utf8");
  assert.equal(source.includes("innerHTML"),false);
  assert.match(source,/check\.checked/);
  assert.match(source,/allow_external_images/);
  assert.match(source,/request_id:crypto\.randomUUID/);
});
