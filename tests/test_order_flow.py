import asyncio

from langchain_core.messages import HumanMessage

from app.graph.main_graph import main_graph


async def run_order_flow() -> None:
    thread_id = 'test-order-flow'
    result = await main_graph.ainvoke(
        {
            'messages': [HumanMessage(content='帮我查询一下订单状态')],
            'user_id': 'user_001',
            'order_id': None,
            'thread_id': thread_id,
            'intent': 'order',
            'refund_status': 'not_started',
            'need_human_intervention': False,
        },
        {'configurable': {'thread_id': thread_id}},
    )

    contents = [str(message.content) for message in result['messages']]
    assert result['order_id'] == 'TB202611120001'
    assert any('TB202611120001' in content for content in contents)
    assert any('shipped' in content for content in contents)
    assert result['need_human_intervention'] is False

    order_id = result['order_id']
    print(f'order_id={order_id}')
    print(f'last_message={contents[-1]}')
    print('ORDER_FLOW_OK')


if __name__ == '__main__':
    asyncio.run(run_order_flow())
