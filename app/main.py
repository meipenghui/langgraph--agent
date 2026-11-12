import logging
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from langchain_core.messages import HumanMessage
from langgraph.types import Command
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse

from app.graph.main_graph import main_graph
from app.graph.state import Intent, RefundStatus

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s - %(message)s',
)


class UTF8JSONResponse(JSONResponse):
    media_type = 'application/json; charset=utf-8'


app = FastAPI(
    title='E-commerce Agent API',
    version='0.1.0',
    default_response_class=UTF8JSONResponse,
)

PENDING_APPROVALS: dict[str, dict[str, Any]] = {}


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    user_id: str | None = None
    order_id: str | None = None
    intent: Intent | None = None
    thread_id: str | None = None


class ChatResponse(BaseModel):
    thread_id: str
    user_id: str
    intent: Intent
    reply: str
    order_id: str | None = None
    order_amount: float | None = None
    refund_status: RefundStatus
    need_human_intervention: bool
    retrieved_context: list[str] = Field(default_factory=list)


class ApprovalRequest(BaseModel):
    user_id: str = Field(min_length=1)
    thread_id: str = Field(min_length=1)
    decision: Literal['approve', 'reject']


class ApprovalResponse(BaseModel):
    thread_id: str
    user_id: str
    decision: Literal['approve', 'reject']
    reply: str
    order_id: str | None = None
    order_amount: float | None = None
    refund_status: RefundStatus
    need_human_intervention: bool


def _reply_from_result(result: dict[str, Any]) -> str:
    messages = result.get('messages') or []
    if not messages:
        return ''
    content = messages[-1].content
    return content if isinstance(content, str) else str(content)


def _interrupt_value(result: dict[str, Any]) -> dict[str, Any]:
    interrupts = result.get('__interrupt__') or []
    if not interrupts:
        return {}
    first = interrupts[0]
    value = getattr(first, 'value', None)
    if value is None and isinstance(first, dict):
        value = first.get('value')
    return value if isinstance(value, dict) else {}


def _register_pending(
    thread_id: str,
    result: dict[str, Any],
    approval: dict[str, Any],
) -> None:
    PENDING_APPROVALS[thread_id] = {
        'thread_id': thread_id,
        'user_id': approval.get('user_id') or result.get('user_id'),
        'order_id': approval.get('order_id') or result.get('order_id'),
        'order_amount': approval.get('order_amount', result.get('order_amount')),
        'intent': approval.get('intent') or result.get('intent'),
        'refund_status': 'pending_human_review',
        'reply': approval.get('reply'),
        'created_at': datetime.now(timezone.utc).isoformat(),
    }


@app.get('/health', response_class=UTF8JSONResponse, tags=['system'])
async def health_check() -> JSONResponse:
    return UTF8JSONResponse(
        content={'status': 'ok'},
        media_type='application/json; charset=utf-8',
    )


@app.post(
    '/chat',
    response_class=UTF8JSONResponse,
    response_model=ChatResponse,
    tags=['chat'],
)
async def chat(payload: ChatRequest) -> JSONResponse:
    user_id = payload.user_id or 'user_001'
    intent = payload.intent or 'unknown'
    thread_id = payload.thread_id or f'thread_{uuid4().hex}'
    print(f'[chat] 收到用户消息: {payload.message!r}')
    print(f'[chat] thread_id={thread_id} 初始 intent: {intent!r}')

    result = await main_graph.ainvoke(
        {
            'messages': [HumanMessage(content=payload.message)],
            'user_id': user_id,
            'order_id': payload.order_id,
            'thread_id': thread_id,
            'order_amount': None,
            'intent': intent,
            'refund_status': 'not_started',
            'need_human_intervention': False,
            'retrieved_context': [],
        },
        {'configurable': {'thread_id': thread_id}},
    )

    interrupted = bool(result.get('__interrupt__'))
    approval = _interrupt_value(result) if interrupted else {}
    reply = approval.get('reply') or _reply_from_result(result)
    if interrupted:
        _register_pending(thread_id, result, approval)
    else:
        PENDING_APPROVALS.pop(thread_id, None)

    response = ChatResponse(
        thread_id=thread_id,
        user_id=approval.get('user_id') or result.get('user_id', user_id),
        intent=approval.get('intent') or result.get('intent', intent),
        reply=reply,
        order_id=approval.get('order_id') or result.get('order_id'),
        order_amount=approval.get('order_amount', result.get('order_amount')),
        refund_status=(
            'pending_human_review'
            if interrupted
            else result.get('refund_status', 'not_started')
        ),
        need_human_intervention=(
            True if interrupted else result['need_human_intervention']
        ),
        retrieved_context=result.get('retrieved_context', []),
    )
    return UTF8JSONResponse(
        content=response.model_dump(mode='json'),
        media_type='application/json; charset=utf-8',
    )


@app.post(
    '/approve',
    response_class=UTF8JSONResponse,
    response_model=ApprovalResponse,
    tags=['hitl'],
)
async def approve(payload: ApprovalRequest) -> JSONResponse:
    config = {'configurable': {'thread_id': payload.thread_id}}
    snapshot = await main_graph.aget_state(config)
    values = snapshot.values or {}
    if not values:
        raise HTTPException(status_code=404, detail='thread not found')
    if values.get('user_id') != payload.user_id:
        raise HTTPException(
            status_code=403,
            detail='user_id does not match the suspended thread',
        )
    if not snapshot.interrupts:
        raise HTTPException(
            status_code=409,
            detail='thread is not waiting for approval',
        )

    print(
        f'[approval] thread_id={payload.thread_id} '
        f'user_id={payload.user_id} decision={payload.decision}'
    )
    result = await main_graph.ainvoke(
        Command(resume=payload.decision),
        config,
    )
    PENDING_APPROVALS.pop(payload.thread_id, None)
    if result.get('__interrupt__'):
        _register_pending(
            payload.thread_id,
            result,
            _interrupt_value(result),
        )

    response = ApprovalResponse(
        thread_id=payload.thread_id,
        user_id=result.get('user_id', payload.user_id),
        decision=payload.decision,
        reply=_reply_from_result(result),
        order_id=result.get('order_id'),
        order_amount=result.get('order_amount'),
        refund_status=result.get('refund_status', 'pending_human_review'),
        need_human_intervention=result.get('need_human_intervention', False),
    )
    return UTF8JSONResponse(
        content=response.model_dump(mode='json'),
        media_type='application/json; charset=utf-8',
    )


@app.get(
    '/pending_approvals',
    response_class=UTF8JSONResponse,
    tags=['hitl'],
)
async def pending_approvals() -> JSONResponse:
    return UTF8JSONResponse(
        content=list(PENDING_APPROVALS.values()),
        media_type='application/json; charset=utf-8',
    )
