import json
import logging
from typing import Any

import httpx
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph

from app.core.config import load_llm_settings
from app.graph.state import AgentState
from app.graph.subgraphs.mcp_result import extract_order_id, extract_payload
from app.mcp_client.client import invoke_mcp_tool

logger = logging.getLogger(__name__)

ORDER_SUMMARY_SYSTEM_PROMPT = (
    '你是电商订单客服。请根据用户问题和订单查询结果，用简洁自然的中文回答。\n'
    '不能输出 JSON、代码块、字段名列表或原始数据结构。\n'
    '必须保留完整订单号；如果订单有 status 原始英文值，也要保留并补充中文解释，'
    '例如 shipped（已发货）。\n'
    '有物流信息时说明物流公司和运单号；有多笔订单时逐条说明。\n'
    '不要编造查询结果中不存在的信息。'
)


def _latest_user_text(state: AgentState) -> str:
    for message in reversed(state['messages']):
        if getattr(message, 'type', None) == 'human':
            content = message.content
            return content if isinstance(content, str) else str(content)
    return ''


def _order_items(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    orders = payload.get('orders')
    if not isinstance(orders, list):
        return []
    return [order for order in orders if isinstance(order, dict)]


def _filter_payload_by_order_id(
    payload: Any,
    requested_order_id: str | None,
) -> Any:
    if not requested_order_id or not isinstance(payload, dict):
        return payload
    orders = _order_items(payload)
    matching = [
        order for order in orders
        if order.get('order_id') == requested_order_id
    ]
    if matching:
        return {**payload, 'orders': matching}
    return {
        'user_id': payload.get('user_id'),
        'found': False,
        'orders': [],
        'requested_order_id': requested_order_id,
    }


def _format_order_fallback(
    payload: Any,
    requested_order_id: str | None,
) -> str:
    orders = _order_items(payload)
    if not orders:
        if requested_order_id:
            return f'未查询到订单 {requested_order_id} 的信息。'
        return '未查询到您的订单信息。'

    lines: list[str] = []
    for order in orders:
        order_id = order.get('order_id') or '未知订单号'
        status = order.get('status')
        status_text = order.get('status_text')
        if status and status_text:
            status_line = f'{status_text}（{status}）'
        else:
            status_line = status_text or status or '未知'
        line = f'您的订单 {order_id} 当前状态为 {status_line}。'

        company = order.get('logistics_company')
        tracking_no = order.get('tracking_no')
        if company and tracking_no:
            line += f'物流公司为 {company}，运单号是 {tracking_no}。'
        elif tracking_no:
            line += f'运单号是 {tracking_no}。'
        lines.append(line)
    return '\n'.join(lines)


def _append_missing_facts(summary: str, payload: Any) -> str:
    missing: list[str] = []
    for order in _order_items(payload):
        order_id = order.get('order_id')
        if isinstance(order_id, str) and order_id not in summary:
            missing.append(f'订单号：{order_id}')
        status = order.get('status')
        if isinstance(status, str) and status not in summary:
            missing.append(f'当前状态：{status}')
    if not missing:
        return summary
    return summary + '\n' + '\n'.join(missing)


def _looks_like_raw_json(text: str) -> bool:
    stripped = text.strip()
    return (
        stripped.startswith('{')
        or stripped.startswith('[')
        or stripped.startswith('```')
        or '```' in stripped
        or ('{' in stripped and '}' in stripped)
    )


async def _summarize_order(user_message: str, payload: Any) -> str:
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
        payload_text = json.dumps(payload, ensure_ascii=False)
        response = await llm.ainvoke(
            [
                SystemMessage(content=ORDER_SUMMARY_SYSTEM_PROMPT),
                HumanMessage(
                    content=(
                        f'用户问题：{user_message}\n'
                        f'订单查询结果：{payload_text}'
                    )
                ),
            ]
        )
        content = response.content
        summary = content.strip() if isinstance(content, str) else str(content)
        if not summary or _looks_like_raw_json(summary):
            raise ValueError('order summary is empty or still raw JSON')
        return _append_missing_facts(summary, payload)
    finally:
        await http_client.aclose()


async def fetch_order_status(state: AgentState) -> dict[str, Any]:
    user_id = state['user_id']
    requested_order_id = state.get('order_id')
    print(f'[order] calling MCP get_order_status for user_id={user_id}')

    result = await invoke_mcp_tool('get_order_status', {'user_id': user_id})
    payload = extract_payload(result)
    payload = _filter_payload_by_order_id(payload, requested_order_id)

    update: dict[str, Any] = {}
    resolved_order_id = requested_order_id or extract_order_id(payload)
    if resolved_order_id is not None:
        update['order_id'] = resolved_order_id

    user_message = _latest_user_text(state)
    try:
        content = await _summarize_order(user_message, payload)
    except Exception:
        logger.exception('[order] failed to summarize MCP result')
        content = _format_order_fallback(payload, resolved_order_id)

    update['messages'] = [AIMessage(content=content)]
    logger.info('[order] MCP result summarized for user_id=%s', user_id)
    return update


builder = StateGraph(AgentState)
builder.add_node('fetch_order_status', fetch_order_status)
builder.add_edge(START, 'fetch_order_status')
builder.add_edge('fetch_order_status', END)

order_subgraph = builder.compile()
