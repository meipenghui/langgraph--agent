from typing import Any

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    'ecommerce-order-mock',
    instructions='Mock order and refund tools for local e-commerce agent development.',
)

_MOCK_ORDERS: dict[str, list[dict[str, Any]]] = {
    'user_001': [
        {
            'order_id': 'TB202611120001',
            'status': 'shipped',
            'status_text': '已发货',
            'payment_status': 'paid',
            'amount': 299.00,
            'logistics_company': '顺丰速运',
            'tracking_no': 'SF1234567890',
            'updated_at': '2026-11-12T09:30:00+08:00',
        },
    ],
    'user_002': [
        {
            'order_id': 'TB202611120002',
            'status': 'pending_shipment',
            'status_text': '待发货',
            'payment_status': 'paid',
            'amount': 89.90,
            'logistics_company': None,
            'tracking_no': None,
            'updated_at': '2026-11-11T20:15:00+08:00',
        },
    ],
    'user_003': [
        {
            'order_id': 'TB202611120003',
            'status': 'delivered',
            'status_text': '已签收',
            'payment_status': 'paid',
            'amount': 699.00,
            'logistics_company': '京东物流',
            'tracking_no': 'JD9876543210',
            'updated_at': '2026-11-10T16:45:00+08:00',
        },
    ],
}


@mcp.tool()
def get_order_status(user_id: str) -> dict[str, Any]:
    orders = _MOCK_ORDERS.get(user_id)
    if orders is None:
        return {
            'user_id': user_id,
            'found': False,
            'orders': [],
        }

    return {
        'user_id': user_id,
        'found': True,
        'orders': orders,
    }


@mcp.tool()
def apply_refund(order_id: str, reason: str = '用户申请退款') -> dict[str, Any]:
    return {
        'success': True,
        'refund_id': f'RF{order_id[-8:]}',
        'order_id': order_id,
        'status': 'refunded',
        'reason': reason,
    }


if __name__ == '__main__':
    mcp.run()
