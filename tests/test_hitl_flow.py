import asyncio
from typing import Any
from uuid import uuid4

import httpx

import app.graph.subgraphs.refund as refund_module
from app.main import app


MOCK_ORDERS = {
    'user_001': {
        'order_id': 'TB202611120001',
        'amount': 299.00,
    },
    'user_003': {
        'order_id': 'TB202611120003',
        'amount': 699.00,
    },
}


async def fake_invoke_mcp_tool(
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    if tool_name == 'get_order_status':
        user_id = arguments['user_id']
        order = MOCK_ORDERS.get(user_id)
        if order is None:
            return {'user_id': user_id, 'found': False, 'orders': []}
        return {
            'user_id': user_id,
            'found': True,
            'orders': [
                {
                    **order,
                    'status': 'delivered',
                    'status_text': '已签收',
                }
            ],
        }

    if tool_name == 'apply_refund':
        order_id = arguments['order_id']
        return {
            'success': True,
            'refund_id': f'RF{order_id[-8:]}',
            'order_id': order_id,
            'status': 'refunded',
        }

    raise AssertionError(f'unexpected MCP tool: {tool_name}')


async def run_hitl_flow() -> None:
    original_invoke = refund_module.invoke_mcp_tool
    refund_module.invoke_mcp_tool = fake_invoke_mcp_tool

    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(
            transport=transport,
            base_url='http://testserver',
        ) as client:
            high_value_thread = f'hitl-{uuid4().hex}'
            chat_response = await client.post(
                '/chat',
                json={
                    'message': '帮我退款 user_003',
                    'user_id': 'user_003',
                    'thread_id': high_value_thread,
                },
            )
            assert chat_response.status_code == 200, chat_response.text
            chat_data = chat_response.json()
            assert chat_data['intent'] == 'refund'
            assert chat_data['refund_status'] == 'pending_human_review'
            assert chat_data['need_human_intervention'] is True
            assert chat_data['order_id'] == 'TB202611120003'
            assert chat_data['order_amount'] == 699.0

            pending_response = await client.get('/pending_approvals')
            assert pending_response.status_code == 200
            pending = pending_response.json()
            assert any(
                item['thread_id'] == high_value_thread
                for item in pending
            )

            approve_response = await client.post(
                '/approve',
                json={
                    'user_id': 'user_003',
                    'thread_id': high_value_thread,
                    'decision': 'approve',
                },
            )
            assert approve_response.status_code == 200, approve_response.text
            approve_data = approve_response.json()
            assert approve_data['refund_status'] == 'completed'
            assert approve_data['need_human_intervention'] is False
            assert '退款申请已提交成功' in approve_data['reply']

            pending_after_approve = (
                await client.get('/pending_approvals')
            ).json()
            assert all(
                item['thread_id'] != high_value_thread
                for item in pending_after_approve
            )

            rejected_thread = f'hitl-{uuid4().hex}'
            rejected_chat = await client.post(
                '/chat',
                json={
                    'message': '帮我退订 user_003',
                    'user_id': 'user_003',
                    'thread_id': rejected_thread,
                },
            )
            assert rejected_chat.status_code == 200, rejected_chat.text
            assert rejected_chat.json()['refund_status'] == (
                'pending_human_review'
            )

            reject_response = await client.post(
                '/approve',
                json={
                    'user_id': 'user_003',
                    'thread_id': rejected_thread,
                    'decision': 'reject',
                },
            )
            assert reject_response.status_code == 200, reject_response.text
            reject_data = reject_response.json()
            assert reject_data['refund_status'] == 'rejected'
            assert reject_data['need_human_intervention'] is False

            pending_after_reject = (
                await client.get('/pending_approvals')
            ).json()
            assert all(
                item['thread_id'] != rejected_thread
                for item in pending_after_reject
            )

            wrong_user_response = await client.post(
                '/approve',
                json={
                    'user_id': 'user_001',
                    'thread_id': high_value_thread,
                    'decision': 'approve',
                },
            )
            assert wrong_user_response.status_code == 403

            duplicate_response = await client.post(
                '/approve',
                json={
                    'user_id': 'user_003',
                    'thread_id': high_value_thread,
                    'decision': 'approve',
                },
            )
            assert duplicate_response.status_code == 409

            missing_response = await client.post(
                '/approve',
                json={
                    'user_id': 'user_003',
                    'thread_id': 'thread-does-not-exist',
                    'decision': 'approve',
                },
            )
            assert missing_response.status_code == 404

            auto_thread = f'hitl-{uuid4().hex}'
            auto_response = await client.post(
                '/chat',
                json={
                    'message': '帮我退款 user_001',
                    'user_id': 'user_001',
                    'thread_id': auto_thread,
                },
            )
            assert auto_response.status_code == 200, auto_response.text
            auto_data = auto_response.json()
            assert auto_data['intent'] == 'refund'
            assert auto_data['refund_status'] == 'completed'
            assert auto_data['need_human_intervention'] is False

            print(f'pending_after_approve={pending_after_approve}')
            print(f'approve_result={approve_data}')
            print(f'reject_result={reject_data}')
            print(f'auto_refund_result={auto_data}')
            print('HITL_FLOW_OK')
    finally:
        refund_module.invoke_mcp_tool = original_invoke


if __name__ == '__main__':
    asyncio.run(run_hitl_flow())
