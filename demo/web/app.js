const conversations = [
  { id: "I-1048", customer: "导购 A", channel: "门店咨询", time: "刚刚", status: "待处理", audience: "employee", question: "顾客询问产品使用方法时，客服可以依据什么资料进行说明？" },
  { id: "I-1051", customer: "导购 B", channel: "收货作业", time: "3 分钟前", status: "待处理", audience: "employee", question: "门店收货时，应重点检查哪些信息？" },
  { id: "I-1056", customer: "客服 C", channel: "售后协作", time: "8 分钟前", status: "待核对", audience: "employee", question: "顾客反映使用后持续红肿刺痛，员工应记录哪些信息并如何处理？" },
  { id: "I-1060", customer: "导购 D", channel: "标签核对", time: "12 分钟前", status: "待处理", audience: "employee", question: "标签和页面信息不一致时，第一步怎么做？" },
];

const state = { snapshot: null, activeConversation: null, currentResult: null, writeback: null };
const $ = (selector) => document.querySelector(selector);

function loadDirectFileSnapshot() {
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = "data/knowledge_snapshot.js";
    script.onload = () => window.__KNOWLEDGE_SNAPSHOT__ ? resolve(window.__KNOWLEDGE_SNAPSHOT__) : reject(new Error("知识快照脚本未提供数据"));
    script.onerror = () => reject(new Error("知识快照脚本载入失败"));
    document.head.appendChild(script);
  });
}

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#039;", '"': "&quot;" })[character]);
}

function formatAnswer(value) {
  return escapeHtml(value).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/\n/g, "<br/>");
}

function renderConversationList() {
  $("#queue-count").textContent = conversations.length;
  $("#conversation-list").innerHTML = conversations.map((item) => `
    <button class="conversation-item ${state.activeConversation?.id === item.id ? "active" : ""}" type="button" data-conversation-id="${item.id}" role="listitem">
      <span class="conversation-item-top"><span>${escapeHtml(item.id)} · ${escapeHtml(item.channel)}</span><span>${escapeHtml(item.time)}</span></span>
      <strong>${escapeHtml(item.customer)} · ${escapeHtml(item.status)}</strong>
      <p>${escapeHtml(item.question)}</p>
    </button>`).join("");
  document.querySelectorAll(".conversation-item").forEach((button) => button.addEventListener("click", () => {
    const item = conversations.find((conversation) => conversation.id === button.dataset.conversationId);
    selectConversation(item);
  }));
}

function renderConversationDetail(item) {
  const employeeQuery = item.audience === "employee";
  $("#conversation-type").textContent = employeeQuery ? "员工检索" : "顾客会话";
  $("#outcome-type").textContent = employeeQuery ? "检索处理" : "回复处理";
  $("#result-title").textContent = employeeQuery ? "检索结果" : "建议回复";
  $("#conversation-title").textContent = item.kind === "employee_query" ? "员工自由检索" : `${item.customer} · ${item.id}`;
  $("#conversation-status").textContent = employeeQuery ? "内部参考" : item.status;
  $("#conversation-detail").classList.remove("empty-conversation");
  $("#conversation-detail").innerHTML = `
    <div class="message customer"><span class="message-meta">${employeeQuery ? `${escapeHtml(item.customer || "员工")}提问 · 知识库检索` : `顾客原话 · ${escapeHtml(item.channel)}`}</span>${escapeHtml(item.question)}</div>
    <p class="conversation-context"><strong>来源：</strong>${employeeQuery ? "员工发起的内部检索，不创建顾客会话、不回写外部渠道。" : "课程 Mock 入站会话。真实部署时由咨询平台 Webhook 传入，不在本页人工复制。"}</p>`;
}

function riskPanel(risks, hasCandidates) {
  if (risks.length) {
    const labels = risks.map((item) => `${item.label}（${item.matched.join("、")}）`).join("；");
    const employeeQuery = state.activeConversation?.audience === "employee";
    const message = employeeQuery ? "已标记风险；本页仅返回员工可核对的内部流程，不形成对客结论。" : "已停止进入答复建议链路；请由客服或导购按现行流程核对。";
    return `<p class="stage-title"><span>入口风险预检</span><span>需人工确认</span></p><div class="risk-strip stop"><span class="status-symbol">!</span><div><strong>${employeeQuery ? "风险已标记，继续查询内部流程" : "已停止进入答复建议链路"}</strong><p>识别到：${escapeHtml(labels)}。${message}</p></div></div>`;
  }
  if (!hasCandidates) return `<p class="stage-title"><span>入口风险预检</span><span>继续核对</span></p><div class="risk-strip review"><span class="status-symbol">?</span><div><strong>未发现入口红线，仍需确认知识依据</strong><p>若无法定位可引用资料，系统将生成待核对项，不补造商品、库存或售后结论。</p></div></div>`;
  return `<p class="stage-title"><span>入口风险预检</span><span>继续检索</span></p><div class="risk-strip"><span class="status-symbol">✓</span><div><strong>未识别入口高风险信号</strong><p>可进入知识检索，仍须核对引用依据与答复边界。</p></div></div>`;
}

function evidencePanel(candidates) {
  if (!candidates.length) return `<details class="evidence-details"><summary>查看依据（资料不足）</summary><div class="risk-strip review"><span class="status-symbol">?</span><div><strong>未定位到可引用的现行资料</strong><p>不能补造商品、库存、效期或售后结论，请由资深员工确认需要补充的资料。</p></div></div></details>`;
  const tags = candidates.map((doc) => {
    const heading = doc.heading || doc.section?.heading || "文档正文";
    const matchText = doc.matches?.join("、") || `相关度 ${Math.round((doc.score || 0) * 100)}%`;
    return `<div class="evidence-tag"><i aria-hidden="true"></i><div><h4>${escapeHtml(doc.category)} · ${escapeHtml(heading)}</h4><p>${escapeHtml(doc.version)} · 生效 ${escapeHtml(doc.effective_date)} · ${escapeHtml(matchText)}</p></div><a href="${escapeHtml(doc.url)}" target="_blank" rel="noreferrer">查看原文</a></div>`;
  }).join("");
  return `<details class="evidence-details"><summary>查看依据（${candidates.length}）</summary><div class="evidence-list">${tags}</div></details>`;
}

function decisionPanel(question, result) {
  const employeeQuery = state.activeConversation?.audience === "employee";
  if (result.status === "out_of_scope") {
    return `<p class="stage-title"><span>处理结论</span><span>不处理内部请求</span></p><div class="decision-card review"><h3>内部配置不对外提供</h3><p>${escapeHtml(result.answer)}</p><span class="handoff-stamp">未检索知识，未生成答复建议</span></div>`;
  }
  if (result.status === "manual_confirmation_required") {
    const reasons = result.risks?.length ? result.risks.map((item) => item.label) : ["资料不足，未命中可引用知识文件"];
    const kind = result.risks?.length ? "stop" : "review";
    const handoffDone = state.writeback?.type === "handoff";
    const action = employeeQuery ? "" : handoffDone ? `<p class="mock-writeback">已模拟提交人工确认单：${escapeHtml(state.writeback.id)}</p>` : `<button class="reply-action handoff" type="button" data-action="handoff">提交人工确认（Mock）</button>`;
    const intro = employeeQuery ? "本次内部检索不能形成确定性结论。请记录下列原因并按现行流程自行核对。" : "本次咨询不能形成对客确定性答复。客服或导购应先核对下列内容，再按现行流程决定是否提交人工确认。";
    return `<p class="stage-title"><span>处理结论</span><span>人工确认</span></p><div class="decision-card ${kind}"><h3>人工确认信息</h3><p>${intro}</p><ul><li>问题：${escapeHtml(question)}</li><li>触发原因：${escapeHtml(reasons.join("；"))}</li><li>待核对项：处理口径、可用依据及后续动作</li></ul>${action}<span class="handoff-stamp">需人工确认，不自动对客发送</span></div>`;
  }
  if (result.status === "service_unavailable") return `<p class="stage-title"><span>处理结论</span><span>服务未启动</span></p><div class="decision-card review"><h3>本机演示服务尚未启动</h3><p>当前页面无法调用检索与 DeepSeek 答复服务。请在 <code>demo</code> 目录运行 <code>python3 rag_server.py</code>，再访问 <code>http://127.0.0.1:8080</code>。</p><span class="handoff-stamp">未生成答复建议</span></div>`;
  const modeLabel = result.mode === "llm_grounded_rag" ? "模型依据答复" : result.mode === "grounded_template_draft" ? "依据答复草案（模型未配置）" : "依据答复草案（模型暂不可用）";
  if (employeeQuery) return `<p class="stage-title"><span>检索结论</span><span>供员工参考</span></p><div class="decision-card"><h3>${modeLabel}</h3><p class="knowledge-answer">${formatAnswer(result.answer)}</p><p>请结合来源与实际业务情况判断；本入口不创建顾客会话、不模拟发送回复。</p><span class="handoff-stamp internal-stamp">仅供内部核对</span></div>`;
  const sent = state.writeback?.type === "message";
  return `<p class="stage-title"><span>建议回复</span><span>${sent ? "已模拟回写" : "待客服审核"}</span></p><div class="decision-card"><h3>${modeLabel}</h3><textarea id="reply-editor" class="reply-editor" aria-label="建议回复，可编辑">${escapeHtml(result.answer)}</textarea><p>请审核并按实际沟通需要微调。依据仅在需要核对时展开查看。</p>${sent ? `<p class="mock-writeback">已模拟回写至原咨询会话：${escapeHtml(state.writeback.id)}</p>` : `<button class="reply-action" type="button" data-action="send">确认回复（Mock）</button>`}</div>`;
}

function renderLedger(documents) {
  $("#source-ledger-list").innerHTML = documents.map((doc) => `<div class="ledger-item"><p>${escapeHtml(doc.category)}</p><strong>${escapeHtml(doc.title.replace(/^\[FDE Mock[^\]]+\]\s*/, ""))}</strong><span class="ledger-meta">${escapeHtml(doc.version)} · ${escapeHtml(doc.effective_date)}</span></div>`).join("");
}

function serviceUnavailable() {
  return { status: "service_unavailable", answer: "", mode: "service_unavailable", risks: [], evidence: [] };
}

async function runInquiry(question) {
  $("#source-status").textContent = "正在检索";
  let result;
  try {
    const audience = state.activeConversation?.audience || "customer";
    const response = await fetch("/api/answer", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question, audience }) });
    if (!response.ok) throw new Error("RAG 服务不可用");
    result = await response.json();
  } catch (error) {
    result = serviceUnavailable();
  }
  $("#source-status").textContent = result.status === "service_unavailable" ? "服务未启动" : "已完成处理";
  $("#empty-state").hidden = true;
  const content = $("#result-content");
  content.hidden = false;
  $("#risk-panel").innerHTML = result.status === "service_unavailable" ? "" : riskPanel(result.risks || [], result.evidence.length > 0);
  $("#evidence-panel").innerHTML = result.status === "service_unavailable" ? "" : evidencePanel(result.evidence || []);
  $("#decision-panel").innerHTML = decisionPanel(question, result);
  state.currentResult = result;
  bindOutcomeActions();
}

function selectConversation(item) {
  state.activeConversation = item;
  state.writeback = null;
  renderConversationList();
  renderConversationDetail(item);
  runInquiry(item.question);
}

function selectEmployeeQuery(question) {
  state.activeConversation = { id: "K-LOCAL", kind: "employee_query", audience: "employee", question };
  state.writeback = null;
  renderConversationList();
  renderConversationDetail(state.activeConversation);
  runInquiry(question);
}

function bindManualEntry() {
  const toggle = $("#manual-entry-toggle");
  const form = $("#manual-entry-form");
  const input = $("#manual-question");
  const error = $("#manual-entry-error");
  toggle.addEventListener("click", () => {
    const opening = form.hidden;
    form.hidden = !opening;
    toggle.setAttribute("aria-expanded", String(opening));
    if (opening) input.focus();
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const question = input.value.trim();
    if (!question) {
      error.textContent = "请输入需要检索的问题。";
      input.focus();
      return;
    }
    error.textContent = "";
    selectEmployeeQuery(question);
  });
}

async function postMock(path, payload) {
  const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  if (!response.ok) throw new Error("模拟接入服务不可用");
  return response.json();
}

function bindOutcomeActions() {
  const action = document.querySelector("[data-action]");
  if (!action || !state.activeConversation) return;
  action.addEventListener("click", async () => {
    action.disabled = true;
    action.textContent = "正在模拟回写…";
    try {
      if (action.dataset.action === "send") {
        const content = $("#reply-editor").value.trim();
        const receipt = await postMock("/api/messages/send", {
          channel: state.activeConversation.channel,
          external_conversation_id: state.activeConversation.id,
          content,
          operator_id: "demo-agent",
          idempotency_key: `${state.activeConversation.id}-${Date.now()}`,
        });
        state.activeConversation.status = "已模拟回写";
        state.writeback = { type: "message", id: receipt.message_id };
      } else {
        const receipt = await postMock("/api/handoffs", {
          external_conversation_id: state.activeConversation.id,
          question: state.activeConversation.question,
          risks: state.currentResult?.risks || [],
          evidence: state.currentResult?.evidence || [],
        });
        state.activeConversation.status = "已提交人工确认";
        state.writeback = { type: "handoff", id: receipt.handoff_id };
      }
      renderConversationList();
      renderConversationDetail(state.activeConversation);
      $("#decision-panel").innerHTML = decisionPanel(state.activeConversation.question, state.currentResult);
    } catch (error) {
      action.disabled = false;
      action.textContent = "模拟回写失败，请重试";
    }
  });
}

async function initialize() {
  try {
    try {
      const response = await fetch("data/knowledge_snapshot.json", { cache: "no-store" });
      if (!response.ok) throw new Error("知识数据未生成");
      state.snapshot = await response.json();
    } catch (fetchError) {
      state.snapshot = await loadDirectFileSnapshot();
    }
    renderLedger(state.snapshot.documents);
  } catch (error) {
    $("#source-status").textContent = "知识数据载入失败";
    $("#source-ledger-list").innerHTML = "<p>请先运行同步脚本生成知识快照。</p>";
  }
  renderConversationList();
  bindManualEntry();
  selectConversation(conversations[0]);
}

initialize();
