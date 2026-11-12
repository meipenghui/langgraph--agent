import logging

from fastapi import FastAPI
from langchain_core.messages import HumanMessage
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


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    user_id: str | None = None
    order_id: str | None = None
    intent: Intent | None = None


class ChatResponse(BaseModel):
    user_id: str
    intent: Intent
    reply: str
    order_id: str | None = None
    order_amount: float | None = None
    refund_status: RefundStatus
    need_human_intervention: bool
    retrieved_context: list[str] = Field(default_factory=list)


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
    print(f'[chat] 收到用户消息: {payload.message!r}')
    print(f'[chat] 初始 intent: {intent!r}')

    result = await main_graph.ainvoke(
        {
            'messages': [HumanMessage(content=payload.message)],
            'user_id': user_id,
            'order_id': payload.order_id,
            'order_amount': None,
            'intent': intent,
            'refund_status': 'not_started',
            'need_human_intervention': False,
            'retrieved_context': [],
        }
    )

    last_message = result['messages'][-1]
    reply = (
        last_message.content
        if isinstance(last_message.content, str)
        else str(last_message.content)
    )

    response = ChatResponse(
        user_id=result.get('user_id', user_id),
        intent=result.get('intent', intent),
        reply=reply,
        order_id=result.get('order_id'),
        order_amount=result.get('order_amount'),
        refund_status=result.get('refund_status', 'not_started'),
        need_human_intervention=result['need_human_intervention'],
        retrieved_context=result.get('retrieved_context', []),
    )
    return UTF8JSONResponse(
        content=response.model_dump(mode='json'),
        media_type='application/json; charset=utf-8',
    )
