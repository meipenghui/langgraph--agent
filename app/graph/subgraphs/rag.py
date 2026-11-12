from typing import Any

from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph

from app.graph.state import AgentState
from app.rag import search_knowledge_base

HUMAN_REQUEST_KEYWORDS = (
    '转人工',
    '人工客服',
    '找人工',
    '真人客服',
    '人工处理',
)

NOT_FOUND_REPLIES = {
    'presale': (
        '抱歉，暂时没有找到与该商品问题匹配的资料。'
        '您可以补充商品名称、型号或具体想了解的信息，我再帮您确认。'
    ),
    'usage': (
        '抱歉，暂时没有找到与该使用方法匹配的说明。'
        '您可以补充商品名称或具体操作步骤，我再帮您确认。'
    ),
}


def _latest_user_text(state: AgentState) -> str:
    for message in reversed(state['messages']):
        if getattr(message, 'type', None) != 'human':
            continue
        content = message.content
        return content if isinstance(content, str) else str(content)
    return ''


def build_rag_subgraph(branch_name: str, intro: str):
    async def retrieve_knowledge(state: AgentState) -> dict[str, Any]:
        query = _latest_user_text(state)
        results = search_knowledge_base(query, top_k=1)
        human_requested = any(
            keyword in query for keyword in HUMAN_REQUEST_KEYWORDS
        )
        context = [
            f'{result.document.title}: {result.document.content}'
            for result in results
        ]

        if context:
            reply = intro + '\n' + '\n'.join(f'- {item}' for item in context)
            if human_requested:
                reply += '\n已按您的要求转接人工客服。'
            need_human_intervention = human_requested
        else:
            if human_requested:
                reply = (
                    '抱歉，暂时没有找到匹配的资料，'
                    '已按您的要求转接人工客服。'
                )
                need_human_intervention = True
            else:
                reply = NOT_FOUND_REPLIES.get(
                    branch_name,
                    '抱歉，暂时没有找到匹配的资料，请补充更多信息后再试。',
                )
                need_human_intervention = False

        print(f'[{branch_name}] query={query} hits={len(context)}')
        return {
            'messages': [AIMessage(content=reply)],
            'retrieved_context': context,
            'need_human_intervention': need_human_intervention,
        }

    builder = StateGraph(AgentState)
    builder.add_node('retrieve_knowledge', retrieve_knowledge)
    builder.add_edge(START, 'retrieve_knowledge')
    builder.add_edge('retrieve_knowledge', END)
    return builder.compile()
