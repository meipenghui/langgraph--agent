from typing import Any

from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph

from app.graph.state import AgentState
from app.graph.subgraphs.mcp_result import (
    extract_first_order,
    extract_order_amount,
    extract_payload,
)
from app.mcp_client.client import invoke_mcp_tool

REFUND_AMOUNT_LIMIT = 500.0


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
        return 'human_review'
    return 'human_review' if amount > REFUND_AMOUNT_LIMIT else 'apply_refund'


async def human_review(state: AgentState) -> dict[str, Any]:
    order_id = state.get('order_id')
    amount = state.get('order_amount')

    if order_id is None:
        reply = '未查询到可退款订单，已转人工处理。'
    elif amount is None:
        reply = f'订单 {order_id} 的金额信息缺失，已转人工处理。'
    else:
        reply = (
            f'订单 {order_id} 金额为 {amount:.2f} 元，'
            '超过 500 元退款规则阈值，已转人工审核。'
        )

    print(f'[refund] human review required for order_id={order_id} amount={amount}')
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
builder.add_node('human_review', human_review)
builder.add_edge(START, 'fetch_order')
builder.add_conditional_edges(
    'fetch_order',
    route_refund_rule,
    {
        'apply_refund': 'apply_refund',
        'human_review': 'human_review',
    },
)
builder.add_edge('apply_refund', END)
builder.add_edge('human_review', END)

refund_subgraph = builder.compile()
