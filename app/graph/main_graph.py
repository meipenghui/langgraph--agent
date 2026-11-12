import json
import logging
import re
from typing import Any

import httpx
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from app.core.config import load_llm_settings
from app.graph.state import AgentState
from app.graph.subgraphs.order import order_subgraph
from app.graph.subgraphs.presale import presale_subgraph
from app.graph.subgraphs.refund import refund_subgraph
from app.graph.subgraphs.usage import usage_subgraph

logger = logging.getLogger(__name__)

SUPPORTED_INTENTS = {'presale', 'order', 'usage', 'refund'}
INTENT_PATTERN = re.compile(
    r'\b(presale|order|usage|refund)\b',
    re.IGNORECASE,
)
USER_ID_PATTERN = re.compile(r'\buser_[0-9A-Za-z]+\b', re.IGNORECASE)
ORDER_ID_PATTERN = re.compile(r'\bTB[0-9A-Za-z]+\b', re.IGNORECASE)
REFUND_KEYWORDS = (
    '退款',
    '退订',
    '退货',
    '退钱',
    '换货',
    '退换货',
    '取消订单',
    '售后',
)

FALLBACK_REPLY = (
    '抱歉，我暂时无法准确判断您的问题类型。'
    '您可以补充说明是想咨询商品与价格、查询订单与物流、'
    '了解商品使用方法，还是申请退款售后，我会继续为您处理。'
)

ROUTER_OUTPUT_EXAMPLE = json.dumps(
    {'intent': 'order', 'user_id': 'user_001', 'order_id': None},
    ensure_ascii=False,
)

ROUTER_SYSTEM_PROMPT = (
    '你是电商客服系统的路由分类与实体提取器。\n'
    '任务一：判断意图，只能从 presale、order、usage、refund 四个标签中选择一个。\n'
    'presale 表示售前咨询：商品价格、颜色、规格、容量、材质、优惠、购买建议。\n'
    'order 表示订单物流：订单状态、订单号、快递、物流、发货、配送、签收、包裹到哪。\n'
    'usage 表示使用咨询：怎么用、安装、连接、配对、清洗、清洁、保养、说明书、故障。\n'
    'refund 表示退款售后：退款、退货、退钱、换货、售后、退换货、退订、取消订单。\n'
    '只要用户原文出现退款、退订、退货、取消订单等字样，意图必须选择 refund，'
    '即使同时提到了订单、快递或物流，也绝不能选择 order。\n'
    '任务二：从用户原文中精准提取被提及的 user_id 和 order_id。\n'
    'user_id 形如 user_001、user_002、user_003；order_id 通常以 TB 开头。\n'
    '只提取原文中逐字出现的值，不要猜测、补全或编造；没有出现时字段值必须为 null。\n'
    '只输出一个 JSON 对象，不要输出 Markdown、代码块、解释或多余文字。\n'
    'JSON 必须包含且仅包含 intent、user_id、order_id 三个字段。\n'
    '输出示例：' + ROUTER_OUTPUT_EXAMPLE
)


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get('text')
                if isinstance(text, str):
                    parts.append(text)
        return ''.join(parts)
    return str(content)


def _latest_user_text(state: AgentState) -> str:
    for message in reversed(state['messages']):
        if getattr(message, 'type', None) == 'human':
            return _message_text(message.content).strip()
    return ''


def _strip_json_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith('```'):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].strip().startswith('```'):
        lines = lines[1:]
    if lines and lines[-1].strip() == '```':
        lines = lines[:-1]
    return '\n'.join(lines).strip()


def _extract_id_from_message(
    value: Any,
    message: str,
    pattern: re.Pattern[str],
) -> str | None:
    if isinstance(value, str):
        candidate = value.strip()
        if candidate and candidate in message and pattern.fullmatch(candidate):
            return candidate
    match = pattern.search(message)
    return match.group(0) if match else None


def _parse_route_result(text: str, message: str) -> dict[str, Any]:
    cleaned = _strip_json_fence(text)
    data: dict[str, Any] = {}
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            data = parsed
    except json.JSONDecodeError:
        pass

    intent = str(data.get('intent') or '').strip().lower()
    if intent not in SUPPORTED_INTENTS:
        match = INTENT_PATTERN.search(cleaned)
        if match is None:
            raise ValueError(f'invalid router response from model: {text!r}')
        intent = match.group(1).lower()

    return {
        'intent': intent,
        'user_id': _extract_id_from_message(
            data.get('user_id'), message, USER_ID_PATTERN
        ),
        'order_id': _extract_id_from_message(
            data.get('order_id'), message, ORDER_ID_PATTERN
        ),
    }


async def classify_route(message: str) -> dict[str, Any]:
    settings = load_llm_settings()
    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(settings.timeout),
        trust_env=settings.trust_env,
    )
    try:
        llm = ChatOpenAI(
            model=settings.model,
            api_key=settings.api_key,
            base_url=settings.base_url,
            temperature=settings.temperature,
            timeout=settings.timeout,
            max_retries=settings.max_retries,
            http_async_client=http_client,
            http_socket_options=(),
        )
        logger.info(
            '[intent] calling DeepSeek model=%s base_url=%s key_loaded=%s trust_env=%s',
            settings.model,
            settings.base_url,
            bool(settings.api_key),
            settings.trust_env,
        )
        response = await llm.ainvoke(
            [
                SystemMessage(content=ROUTER_SYSTEM_PROMPT),
                HumanMessage(content=message),
            ]
        )
        text = _message_text(response.content).strip()
        print(f'LLM 原始输出: {text!r}')
        logger.info('[intent] model response=%r', text)
        decision = _parse_route_result(text, message)
        decision_intent = decision['intent']
        print(f'LLM 分类结果: {decision_intent}')
        return decision
    finally:
        await http_client.aclose()


async def classify_intent(message: str) -> str:
    decision = await classify_route(message)
    return str(decision['intent'])


async def router_node(state: AgentState) -> dict[str, Any]:
    intent = state.get('intent', 'unknown')
    user_id = state['user_id']
    message = _latest_user_text(state)

    if any(keyword in message for keyword in REFUND_KEYWORDS):
        matched_keyword = next(
            keyword for keyword in REFUND_KEYWORDS if keyword in message
        )
        print(f'[router] 关键词强制命中: {matched_keyword} -> refund')
        forced_update: dict[str, Any] = {'intent': 'refund'}
        user_match = USER_ID_PATTERN.search(message)
        order_match = ORDER_ID_PATTERN.search(message)
        if user_match:
            forced_update['user_id'] = user_match.group(0)
        if order_match:
            forced_update['order_id'] = order_match.group(0)
        forced_user_id = forced_update.get('user_id', user_id)
        print(
            f'[router] forced refund intent user_id='
            f'{forced_user_id}'
        )
        return forced_update

    if intent in SUPPORTED_INTENTS:
        update: dict[str, Any] = {'intent': intent}
        user_match = USER_ID_PATTERN.search(message)
        order_match = ORDER_ID_PATTERN.search(message)
        if user_match:
            update['user_id'] = user_match.group(0)
        if order_match:
            update['order_id'] = order_match.group(0)
        resolved_user_id = update.get('user_id', user_id)
        print(
            f'[router] explicit intent={intent} '
            f'user_id={resolved_user_id}'
        )
        return update

    if not message:
        print(f'[router] empty message, intent=unknown user_id={user_id}')
        return {'intent': 'unknown'}

    update: dict[str, Any] = {'intent': 'unknown'}
    try:
        decision = await classify_route(message)
        update['intent'] = decision['intent']
        if decision.get('user_id'):
            update['user_id'] = decision['user_id']
        if decision.get('order_id'):
            update['order_id'] = decision['order_id']
    except Exception:
        logger.exception(
            '[router] DeepSeek route classification failed user_id=%s',
            user_id,
        )

    classified_intent = update['intent']
    resolved_user_id = update.get('user_id', user_id)
    resolved_order_id = update.get('order_id')
    print(
        f'[router] classified intent={classified_intent} '
        f'user_id={resolved_user_id} order_id={resolved_order_id}'
    )
    return update


def route_intent(state: AgentState) -> str:
    intent = state['intent']
    return intent if intent in SUPPORTED_INTENTS else 'unknown'


async def fallback_node(state: AgentState) -> dict[str, Any]:
    intent = state['intent']
    print(f'[fallback] unsupported intent={intent}')
    return {
        'messages': [AIMessage(content=FALLBACK_REPLY)],
        'need_human_intervention': False,
    }


builder = StateGraph(AgentState)
builder.add_node('router', router_node)
builder.add_node('presale', presale_subgraph)
builder.add_node('order', order_subgraph)
builder.add_node('usage', usage_subgraph)
builder.add_node('refund', refund_subgraph)
builder.add_node('fallback', fallback_node)

builder.add_edge(START, 'router')
builder.add_conditional_edges(
    'router',
    route_intent,
    {
        'presale': 'presale',
        'order': 'order',
        'usage': 'usage',
        'refund': 'refund',
        'unknown': 'fallback',
    },
)
builder.add_edge('presale', END)
builder.add_edge('order', END)
builder.add_edge('usage', END)
builder.add_edge('refund', END)
builder.add_edge('fallback', END)

checkpointer = MemorySaver()
main_graph = builder.compile(checkpointer=checkpointer)
