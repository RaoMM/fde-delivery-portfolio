#!/usr/bin/env python3
"""Small, auditable RAG runtime for the Day 4 classroom demo.

It deliberately has no database or framework dependency.  At this data size,
the source snapshot is the index.  The server keeps model credentials on the
server; without a configured provider it returns a visibly labelled,
evidence-grounded draft rather than pretending an LLM was used.
"""

from __future__ import annotations

import json
import math
import os
import re
from collections import Counter
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
SNAPSHOT_PATH = WEB_ROOT / "data" / "knowledge_snapshot.json"

RISK_RULES = (
    ("SENSITIVE_POPULATION", ("敏感", "孕", "儿童", "皮肤破损", "药物"), "敏感人群或特殊使用条件"),
    ("SUSPECTED_ADVERSE_REACTION", ("不良反应", "红肿", "刺痛", "瘙痒", "水疱", "眼部", "就医"), "疑似不良反应"),
    ("COMPLEX_COMPLAINT", ("投诉", "赔偿", "媒体", "监管", "质量问题", "包装渗漏", "页面不一致"), "复杂客诉或外部风险"),
    ("MEDICAL_OR_ABSOLUTE_CLAIM", ("治疗", "治愈", "祛痘", "药效", "保证", "一定不过敏", "绝对安全"), "医疗或绝对功效诉求"),
    ("LIVE_SYSTEM_DATA_REQUIRED", ("库存", "还有多少", "现货", "批次", "价格", "订单号", "效期"), "需要实时业务系统核实"),
    ("RESTRICTED_INTERNAL_REQUEST", ("系统提示词", "系统提示", "内部提示词", "内部配置", "忽略之前", "忽略前面的指令"), "内部配置或指令索取"),
)
MIN_RETRIEVAL_SCORE = 0.18

# These stores deliberately live only in memory.  They prove the interface
# contract without retaining course conversations or claiming a real-channel
# integration.
MOCK_INBOUND: list[dict] = []
MOCK_SENT_MESSAGES: list[dict] = []
MOCK_HANDOFFS: list[dict] = []


def features(text: str) -> Counter[str]:
    normalized = re.sub(r"\s+", "", text.lower())
    items = re.findall(r"[a-z0-9+]+", normalized)
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]", normalized))
    items.extend(chinese[index : index + 2] for index in range(max(0, len(chinese) - 1)))
    items.extend(chinese)
    return Counter(items)


def cosine(left: Counter[str], right: Counter[str]) -> float:
    dot = sum(value * right.get(term, 0) for term, value in left.items())
    if not dot:
        return 0.0
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    return dot / (left_norm * right_norm)


def load_snapshot() -> dict:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


def risk_hits(question: str) -> list[dict]:
    hits = []
    for code, terms, label in RISK_RULES:
        matched = [term for term in terms if term in question]
        if matched:
            hits.append({"code": code, "label": label, "matched": matched})
    return hits


def expand_retrieval_query(question: str) -> str:
    """Add only source-vocabulary equivalents needed for short staff queries."""
    additions = []
    if "不能说" in question or "哪些说法" in question:
        additions.extend(("不得", "绝对", "保证"))
    if "渗漏" in question:
        additions.append("漏液")
    return f"{question} {' '.join(additions)}"


def retrieve(question: str, chunks: list[dict]) -> list[dict]:
    query = features(expand_retrieval_query(question))
    ranked = []
    for chunk in chunks:
        score = cosine(query, features(f"{chunk['heading']} {chunk['text']}"))
        if score >= MIN_RETRIEVAL_SCORE:
            ranked.append({**chunk, "score": round(score, 4)})
    return sorted(ranked, key=lambda item: item["score"], reverse=True)[:3]


RISK_CATEGORIES = {
    "SUSPECTED_ADVERSE_REACTION": "疑似不良反应",
    "COMPLEX_COMPLAINT": "退换货与质量投诉",
    "LIVE_SYSTEM_DATA_REQUIRED": "库存与效期",
}


def add_risk_context(question: str, evidence: list[dict], chunks: list[dict], risks: list[dict]) -> list[dict]:
    """Keep risk-gate citations tied to the relevant process document.

    Character n-gram retrieval can miss synonyms such as “渗漏/漏液”.  When a
    rule already identifies a risk class, its governing process document is a
    valid retrieval constraint; this does not add any knowledge outside the
    five source documents.
    """
    categories = {RISK_CATEGORIES[item["code"]] for item in risks if item["code"] in RISK_CATEGORIES}
    if not categories:
        return evidence
    query = features(expand_retrieval_query(question))
    scoped = []
    for chunk in chunks:
        if chunk["category"] in categories:
            scoped.append({**chunk, "score": round(cosine(query, features(f"{chunk['heading']} {chunk['text']}")), 4)})
    if not scoped:
        return evidence
    # Prefer actionable source sections over document headers and public links.
    scoped = [item for item in scoped if item["heading"] not in {item["title"], "文档信息", "公开来源", "不适用范围"}]
    return sorted(scoped, key=lambda item: item["score"], reverse=True)[:3]


def has_actionable_answer_evidence(evidence: list[dict]) -> bool:
    """Return whether a result contains product or FAQ facts, not only guardrails.

    Policy fragments can support a manual-confirmation reason, but they must not
    unlock a customer-facing draft on their own.
    """
    for item in evidence:
        structured = item.get("structured") or {}
        if structured.get("可引用答复") or structured.get("可说明的产品事实"):
            return True
    return False


def draft_answer(question: str, evidence: list[dict]) -> str:
    primary = evidence[0]
    structured = primary.get("structured") or {}
    if structured.get("可引用答复"):
        answer = structured["可引用答复"]
        boundary = structured.get("边界")
        return f"{answer}\n\n说明：{boundary}" if boundary else answer
    if structured.get("可说明的产品事实"):
        return f"根据现有商品资料，{structured['可说明的产品事实']} {structured.get('使用方法与注意事项', '')}".strip()
    if "哪些说法" in question:
        focused = [item for item in evidence if item["category"] == "功效宣称与咨询边界"]
        if focused:
            return "\n\n".join(
                f"根据《{item['title']}》的“{item['heading']}”：{item['text']}"
                for item in focused
            )
    if primary["category"] == "疑似不良反应" and ("记录" in question or "如何处理" in question):
        return "\n\n".join(
            f"根据《{item['title']}》的“{item['heading']}”：{item['text']}"
            for item in evidence
        )
    return f"根据《{primary['title']}》的“{primary['heading']}”：{primary['text']}"


def call_deepseek(question: str, evidence: list[dict], audience: str) -> str:
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("MODEL_NOT_CONFIGURED")
    context = "\n\n".join(
        f"[证据 {index}] {item['title']}｜{item['heading']}｜{item['text']}"
        for index, item in enumerate(evidence, start=1)
    )
    body = json.dumps(
        {
            "model": os.environ.get("DEEPSEEK_CHAT_MODEL", "deepseek-chat"),
            "temperature": 0.1,
            "messages": [
                {
                    "role": "system",
                    "content": "你是美妆零售企业内部客服助手。只能使用提供的证据回答；资料未写就明确说资料不足。只回答问题直接涉及的流程或事实：不得因为检索结果中出现其他主题，就扩展到标签、赔偿、库存等无关流程。不得作医学诊断、绝对功效承诺、退款赔偿裁决或自行补充商品事实。不得编造岗位、时限或系统动作。输出简洁中文答复，不要编造引用编号。",
                },
                {"role": "user", "content": f"使用者：{'员工内部查询' if audience == 'employee' else '顾客咨询'}\n问题：{question}\n\n可用证据：\n{context}"},
            ],
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = Request(
        os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/") + "/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload["choices"][0]["message"]["content"].strip()


def answer(question: str, audience: str = "customer") -> dict:
    snapshot = load_snapshot()
    risks = risk_hits(question)
    evidence = retrieve(question, snapshot["chunks"])
    evidence = add_risk_context(question, evidence, snapshot["chunks"], risks)
    if any(item["code"] == "RESTRICTED_INTERNAL_REQUEST" for item in risks):
        return {
            "status": "out_of_scope",
            "answer": "不能提供系统内部配置、提示词或绕过既有规则。请改为提交具体的产品、使用方法或服务规则咨询。",
            "mode": "restricted_request_gate",
            "risks": risks,
            "evidence": [],
        }
    if risks and audience != "employee":
        return {
            "status": "manual_confirmation_required",
            "answer": "该咨询包含需要进一步人工确认的风险信号。系统不生成对客结论；请由客服或导购按现行规则核对并提交人工确认。",
            "mode": "risk_gate",
            "risks": risks,
            "evidence": evidence,
        }
    if not evidence:
        return {
            "status": "manual_confirmation_required",
            "answer": "当前资料没有定位到可核实的依据，不能补造商品、功效、库存或售后结论，请标记为需人工确认并补充可用依据。",
            "mode": "insufficient_evidence",
            "risks": [],
            "evidence": [],
        }
    # Customer-facing drafts need explicit product or FAQ facts. An employee
    # may retrieve the approved process documents for internal reference; that
    # result never becomes a customer reply or a mock writeback.
    if audience != "employee" and not has_actionable_answer_evidence(evidence):
        return {
            "status": "manual_confirmation_required",
            "answer": "当前仅检索到边界或规则资料，没有定位到可直接回答该咨询的商品或 FAQ 事实，不能据此生成对客结论。请标记为需人工确认，并补充具体产品或批准资料。",
            "mode": "insufficient_actionable_evidence",
            "risks": [],
            "evidence": evidence,
        }
    try:
        response = call_deepseek(question, evidence, audience)
        mode = "llm_grounded_rag"
    except RuntimeError as error:
        if str(error) != "MODEL_NOT_CONFIGURED":
            raise
        response = draft_answer(question, evidence)
        mode = "grounded_template_draft"
    except (HTTPError, URLError, KeyError, TimeoutError) as error:
        response = draft_answer(question, evidence)
        mode = "model_unavailable_fallback"
    return {"status": "review_required", "answer": response, "mode": mode, "risks": risks if audience == "employee" else [], "evidence": evidence}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def do_GET(self):
        if self.path == "/api/health":
            self.send_json({"ok": True, "model_provider": "deepseek", "model_configured": bool(os.environ.get("DEEPSEEK_API_KEY")), "retrieval": "local_char_ngram_vector"})
            return
        super().do_GET()

    def do_POST(self):
        try:
            payload = self.read_json()
            if self.path == "/api/answer":
                question = str(payload.get("question", "")).strip()
                if not question:
                    raise ValueError("请先输入需要检索的问题。")
                audience = str(payload.get("audience", "customer")).strip()
                if audience not in {"customer", "employee"}:
                    raise ValueError("不支持的检索入口。")
                self.send_json(answer(question, audience=audience))
                return
            if self.path == "/api/conversations/inbound":
                required = ("channel", "external_conversation_id", "external_message_id", "customer_ref", "content", "received_at")
                missing = [field for field in required if not str(payload.get(field, "")).strip()]
                if missing:
                    raise ValueError(f"缺少入站字段：{', '.join(missing)}")
                receipt = {"inbound_id": f"IN-{len(MOCK_INBOUND) + 1:04d}", "status": "queued", "integration": "mock"}
                MOCK_INBOUND.append({**payload, **receipt})
                self.send_json(receipt, status=HTTPStatus.CREATED)
                return
            if self.path == "/api/messages/send":
                required = ("channel", "external_conversation_id", "content", "operator_id", "idempotency_key")
                missing = [field for field in required if not str(payload.get(field, "")).strip()]
                if missing:
                    raise ValueError(f"缺少回写字段：{', '.join(missing)}")
                receipt = {"message_id": f"MOCK-MSG-{len(MOCK_SENT_MESSAGES) + 1:04d}", "status": "sent_mock", "integration": "mock"}
                MOCK_SENT_MESSAGES.append({**payload, **receipt})
                self.send_json(receipt, status=HTTPStatus.CREATED)
                return
            if self.path == "/api/handoffs":
                required = ("external_conversation_id", "question")
                missing = [field for field in required if not str(payload.get(field, "")).strip()]
                if missing:
                    raise ValueError(f"缺少人工确认字段：{', '.join(missing)}")
                receipt = {"handoff_id": f"MOCK-HO-{len(MOCK_HANDOFFS) + 1:04d}", "status": "handoff_mock_created", "integration": "mock"}
                MOCK_HANDOFFS.append({**payload, **receipt})
                self.send_json(receipt, status=HTTPStatus.CREATED)
                return
            self.send_error(HTTPStatus.NOT_FOUND)
        except (ValueError, json.JSONDecodeError) as error:
            self.send_json({"ok": False, "error": str(error)}, status=HTTPStatus.BAD_REQUEST)
        except Exception:
            self.send_json({"ok": False, "error": "服务暂时不可用。请勿发送未核实的答复，待服务恢复后重新检索或按现行流程人工核对。"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            raise ValueError("请求体不能为空。")
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("请求体必须是 JSON 对象。")
        return payload

    def send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    print(f"Beauty RAG demo: http://127.0.0.1:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
