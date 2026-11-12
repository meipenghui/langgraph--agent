import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import httpx
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

import app.graph.subgraphs.order as order_module
import app.graph.subgraphs.refund as refund_module
from app.core.config import LLMSettings
from app.main import app


FAKE_ORDER_TOKENS = ['您的', '订单', '已发货。']


class FakeStreamingChatModel(BaseChatModel):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__()

    @property
    def _llm_type(self) -> str:
        return 'fake-streaming-chat-model'

    def _generate(
        self,
        messages: list[Any],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        message = AIMessage(content=''.join(FAKE_ORDER_TOKENS))
        return ChatResult(generations=[ChatGeneration(message=message)])

    async def _astream(
        self,
        messages: list[Any],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        for token in FAKE_ORDER_TOKENS:
            chunk = ChatGenerationChunk(message=AIMessageChunk(content=token))
            if run_manager is not None:
                await run_manager.on_llm_new_token(token, chunk=chunk)
            yield chunk


async def fake_refund_mcp(
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    if tool_name == 'get_order_status':
        return {
            'user_id': 'user_003',
            'found': True,
            'orders': [
                {
                    'order_id': 'TB202611120003',
                    'amount': 699.0,
                    'status': 'delivered',
                }
            ],
        }
    if tool_name == 'send_im_notification':
        return {
            'success': True,
            'mock': True,
            'platform': 'mock',
        }
    raise AssertionError(f'unexpected MCP tool: {tool_name}')


async def fake_order_mcp(
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    if tool_name == 'get_order_status':
        return {
            'user_id': 'user_001',
            'found': True,
            'orders': [
                {
                    'order_id': 'TB202611120001',
                    'amount': 299.0,
                    'status': 'shipped',
                    'status_text': '已发货',
                    'logistics_company': '顺丰速运',
                    'tracking_no': 'SF1234567890',
                }
            ],
        }
    raise AssertionError(f'unexpected MCP tool: {tool_name}')


def parse_sse(text: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    event_name = 'message'
    data_lines: list[str] = []

    for line in text.splitlines():
        if line == '':
            if data_lines:
                payload = json.loads('\n'.join(data_lines))
                event_name = payload.get('type') or event_name
                events.append((event_name, payload))
            event_name = 'message'
            data_lines = []
            continue
        if line.startswith('event:'):
            event_name = line[6:].strip()
        elif line.startswith('data:'):
            data_lines.append(line[5:].lstrip())

    if data_lines:
        payload = json.loads('\n'.join(data_lines))
        event_name = payload.get('type') or event_name
        events.append((event_name, payload))
    return events


async def run_streaming_flow() -> None:
    original_refund_invoke = refund_module.invoke_mcp_tool
    original_order_invoke = order_module.invoke_mcp_tool
    original_chat_openai = order_module.ChatOpenAI
    original_load_settings = order_module.load_llm_settings

    refund_module.invoke_mcp_tool = fake_refund_mcp
    order_module.invoke_mcp_tool = fake_order_mcp
    order_module.ChatOpenAI = FakeStreamingChatModel
    order_module.load_llm_settings = lambda: LLMSettings(
        model='fake',
        api_key='fake',
        base_url='http://fake',
    )

    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(
            transport=transport,
            base_url='http://testserver',
        ) as client:
            hitl_thread = f'stream-hitl-{uuid4().hex}'
            hitl_response = await client.post(
                '/chat_stream',
                json={
                    'message': '帮我退款 user_003',
                    'user_id': 'user_003',
                    'thread_id': hitl_thread,
                },
            )
            assert hitl_response.status_code == 200, hitl_response.text
            assert hitl_response.headers['content-type'].startswith(
                'text/event-stream'
            )
            assert hitl_response.headers['x-accel-buffering'] == 'no'
            assert hitl_response.text.startswith('data: ')
            hitl_events = parse_sse(hitl_response.text)
            assert hitl_events[0][0] == 'ready'
            assert hitl_events[-1][0] == 'final'
            hitl_final = hitl_events[-1][1]
            assert hitl_final['need_human_intervention'] is True
            assert hitl_final['refund_status'] == 'pending_human_review'
            assert hitl_final['order_amount'] == 699.0
            assert '人工审核' in hitl_final['reply']

            pending = (await client.get('/pending_approvals')).json()
            assert any(
                item['thread_id'] == hitl_thread for item in pending
            )

            order_thread = f'stream-order-{uuid4().hex}'
            order_response = await client.post(
                '/chat_stream',
                json={
                    'message': '帮我查询订单 user_001',
                    'user_id': 'user_001',
                    'intent': 'order',
                    'thread_id': order_thread,
                },
            )
            assert order_response.status_code == 200, order_response.text
            order_events = parse_sse(order_response.text)
            token_text = ''.join(
                payload['text']
                for event, payload in order_events
                if event == 'token'
            )
            assert token_text == ''.join(FAKE_ORDER_TOKENS)

            order_final = order_events[-1][1]
            assert order_events[-1][0] == 'final'
            assert order_final['need_human_intervention'] is False
            assert order_final['refund_status'] == 'not_started'
            assert order_final['reply'].startswith(token_text)
            assert 'TB202611120001' in order_final['reply']

            print(f'hitl_stream_events={hitl_events}')
            print(f'order_stream_events={order_events}')
            print('STREAM_FLOW_OK')
    finally:
        refund_module.invoke_mcp_tool = original_refund_invoke
        order_module.invoke_mcp_tool = original_order_invoke
        order_module.ChatOpenAI = original_chat_openai
        order_module.load_llm_settings = original_load_settings


if __name__ == '__main__':
    asyncio.run(run_streaming_flow())
