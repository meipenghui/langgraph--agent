from typing import Annotated, Literal, NotRequired, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

Intent = Literal['presale', 'order', 'usage', 'refund', 'unknown']
RefundStatus = Literal[
    'not_started',
    'collecting_info',
    'checking_eligibility',
    'pending_human_review',
    'approved',
    'rejected',
    'completed',
]


class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    user_id: str
    order_id: str | None
    order_amount: NotRequired[float | None]
    intent: Intent
    refund_status: RefundStatus
    need_human_intervention: bool
    retrieved_context: NotRequired[list[str]]
