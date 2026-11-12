import logging
from typing import Any

from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.core.config import load_im_settings
from app.graph.state import AgentState
from app.graph.subgraphs.mcp_result import (
    extract_first_order,
    extract_order_amount,
    extract_payload,
)
from app.mcp_client.client import invoke_mcp_tool

REFUND_AMOUNT_LIMIT = 500.0
logger = logging.getLogger(__name__)


def _pending_reply(order_id: str | None, amount: float | None) -> str:
    return (
        f'订单 {order_id} 金额为 {amount:.2f} 元，'
        '超过 500 元退款规则阈值，已提交人工审核，请等待审批结果。'
    )


async def _notify_pending_approval(
    state: AgentState,
    order_id: str,
    amount: float,
) -> None:
    settings = load_im_settings()
    arguments: dict[str, Any] = {
        'user_id': state['user_id'],
        'amount': amount,
        'order_id': order_id,
        'reason': '用户申请退款，金额超过 500 元，需要人工审批',
    }
    if settings.webhook_url:
        arguments['webhook_url'] = settings.webhook_url

    try:
        result = await invoke_mcp_tool('send_im_notification', arguments)
        logger.info(
            '[refund] IM notification sent order_id=%s result=%s',
            order_id,
            result,
        )
    except Exception:
        logger.exception(
            '[refund] IM notification failed order_id=%s',
            order_id,
        )


async def fetch_order(state: AgentState) -> dict[str, Any]:
    user_id = state['user_id']
    print(f'[refund] calling MCP get_order_status for user_id={user_id}')

    result = await invoke_mcp_tool('get_order_status', {'user_id': user_id})
    payload = extract_payload(result)
    order = extract_first_order(payload)
    if order is None:
        print('[refund] no refundable order found')
        return {
            'refund_status': 'checking_eligibility',
            'need_human_intervention': True,
        }

    order_id = order.get('order_id')
    amount = extract_order_amount(payload)
    update: dict[str, Any] = {
        'refund_status': 'checking_eligibility',
        'need_human_intervention': False,
    }
    if isinstance(order_id, str):
        update['order_id'] = order_id
    if amount is not None:
        update['order_amount'] = amount

    print(f'[refund] order_id={order_id} amount={amount}')
    return update


def route_refund_rule(state: AgentState) -> str:
    amount = state.get('order_amount')
    order_id = state.get('order_id')
    if order_id is None or amount is None:
        return 'missing_info'
    return 'human_review' if amount > REFUND_AMOUNT_LIMIT else 'apply_refund'


async def mark_pending(state: AgentState) -> dict[str, Any]:
    order_id = state.get('order_id')
    amount = state.get('order_amount')
    reply = _pending_reply(order_id, amount)
    print(f'[refund] pending human approval for order_id={order_id} amount={amount}')
    if isinstance(order_id, str) and amount is not None:
        await _notify_pending_approval(state, order_id, amount)
    return {
        'messages': [AIMessage(content=reply)],
        'need_human_intervention': True,
        'refund_status': 'pending_human_review',
    }


async def await_approval(state: AgentState) -> dict[str, Any]:
    order_id = state.get('order_id')
    amount = state.get('order_amount')
    pending_reply = _pending_reply(order_id, amount)
    print(f'[refund] interrupting graph for approval order_id={order_id}')
    decision = interrupt(
        {
            'type': 'refund_approval',
            'thread_id': state.get('thread_id'),
            'user_id': state['user_id'],
            'order_id': order_id,
            'order_amount': amount,
            'intent': 'refund',
            'reply': pending_reply,
            'message': '退款金额超过 500 元，需要人工审批',
        }
    )

    normalized = decision.get('decision') if isinstance(decision, dict) else decision
    normalized = str(normalized).strip().lower()
    print(f'[refund] approval decision={normalized} order_id={order_id}')

    if normalized == 'approve':
        return await apply_refund(state)
    if normalized == 'reject':
        reply = f'订单 {order_id} 的退款申请未通过人工审核，已通知您。'
        rejected_user_id = state['user_id']
        print(f'[refund] rejected and user notified user_id={rejected_user_id}')
        return {
            'messages': [AIMessage(content=reply)],
            'need_human_intervention': False,
            'refund_status': 'rejected',
        }
    raise ValueError(f'unsupported approval decision: {decision!r}')


async def missing_info(state: AgentState) -> dict[str, Any]:
    order_id = state.get('order_id')
    amount = state.get('order_amount')

    if order_id is None:
        reply = '未查询到可退款订单，已转人工处理。'
    elif amount is None:
        reply = f'订单 {order_id} 的金额信息缺失，已转人工处理。'
    else:
        reply = f'订单 {order_id} 的退款信息不完整，已转人工处理。'

    print(f'[refund] missing refund information order_id={order_id} amount={amount}')
    return {
        'messages': [AIMessage(content=reply)],
        'need_human_intervention': True,
        'refund_status': 'pending_human_review',
    }


async def apply_refund(state: AgentState) -> dict[str, Any]:
    order_id = state['order_id']
    print(f'[refund] calling MCP apply_refund for order_id={order_id}')

    result = await invoke_mcp_tool(
        'apply_refund',
        {'order_id': order_id, 'reason': '用户申请退款'},
    )
    payload = extract_payload(result)
    success = isinstance(payload, dict) and payload.get('success') is True

    if success:
        refund_id = payload.get('refund_id', 'unknown')
        reply = f'订单 {order_id} 退款申请已提交成功，退款单号：{refund_id}。'
        return {
            'messages': [AIMessage(content=reply)],
            'need_human_intervention': False,
            'refund_status': 'completed',
        }

    reply = f'订单 {order_id} 退款提交失败，已转人工处理。'
    return {
        'messages': [AIMessage(content=reply)],
        'need_human_intervention': True,
        'refund_status': 'pending_human_review',
    }


builder = StateGraph(AgentState)
builder.add_node('fetch_order', fetch_order)
builder.add_node('apply_refund', apply_refund)
builder.add_node('mark_pending', mark_pending)
builder.add_node('await_approval', await_approval)
builder.add_node('missing_info', missing_info)
builder.add_edge(START, 'fetch_order')
builder.add_conditional_edges(
    'fetch_order',
    route_refund_rule,
    {
        'apply_refund': 'apply_refund',
        'human_review': 'mark_pending',
        'missing_info': 'missing_info',
    },
)
builder.add_edge('mark_pending', 'await_approval')
builder.add_edge('apply_refund', END)
builder.add_edge('await_approval', END)
builder.add_edge('missing_info', END)

refund_subgraph = builder.compile()
