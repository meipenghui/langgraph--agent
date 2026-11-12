import os
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv(Path(__file__).resolve().parents[1] / '.env')

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


def _build_notification_text(
    user_id: str,
    amount: float,
    order_id: str,
    reason: str,
) -> str:
    return (
        '### 退款人工审批提醒\n'
        f'- 用户 ID：{user_id}\n'
        f'- 订单号：{order_id}\n'
        f'- 退款金额：¥{amount:.2f}\n'
        f'- 退款原因：{reason}\n'
        '- 处理状态：等待人工审批\n\n'
        '请及时登录后台完成审批。'
    )


def _detect_webhook_platform(webhook_url: str) -> str:
    normalized = webhook_url.lower()
    if 'dingtalk' in normalized or 'oapi.dingtalk.com' in normalized:
        return 'dingtalk'
    return 'feishu'


def _build_webhook_payload(platform: str, text: str) -> dict[str, Any]:
    if platform == 'dingtalk':
        return {
            'msgtype': 'markdown',
            'markdown': {
                'title': '退款人工审批提醒',
                'text': text,
            },
        }
    return {
        'msg_type': 'text',
        'content': {'text': text},
    }


def _provider_error(platform: str, body: Any) -> str | None:
    if not isinstance(body, dict):
        return None
    if platform == 'dingtalk':
        code = body.get('errcode')
        if code not in (None, 0, '0'):
            return f"errcode={code}, errmsg={body.get('errmsg', '')}"
        return None

    code = body.get('code', body.get('StatusCode'))
    if code not in (None, 0, '0'):
        return f"code={code}, msg={body.get('msg', '')}"
    return None


@mcp.tool()
def send_im_notification(
    user_id: str,
    amount: float,
    order_id: str,
    reason: str,
    webhook_url: str | None = None,
) -> dict[str, Any]:
    text = _build_notification_text(user_id, amount, order_id, reason)
    target_url = (webhook_url or os.getenv('IM_WEBHOOK_URL', '')).strip()

    if not target_url:
        print(
            '[send_im_notification][MOCK] '
            f'user_id={user_id} order_id={order_id} amount={amount:.2f}\n{text}'
        )
        return {
            'success': True,
            'mock': True,
            'platform': 'mock',
            'message': text,
        }

    platform = _detect_webhook_platform(target_url)
    payload = _build_webhook_payload(platform, text)
    try:
        response = httpx.post(target_url, json=payload, timeout=10.0)
        response.raise_for_status()
        try:
            body = response.json()
        except ValueError:
            body = {}

        error = _provider_error(platform, body)
        if error:
            print(
                '[send_im_notification][ERROR] '
                f'platform={platform} order_id={order_id} {error}'
            )
            return {
                'success': False,
                'mock': False,
                'platform': platform,
                'status_code': response.status_code,
                'error': error,
            }

        print(
            '[send_im_notification][SENT] '
            f'platform={platform} order_id={order_id} '
            f'status_code={response.status_code}'
        )
        return {
            'success': True,
            'mock': False,
            'platform': platform,
            'status_code': response.status_code,
            'message': text,
        }
    except httpx.HTTPError as exc:
        print(
            '[send_im_notification][ERROR] '
            f'platform={platform} order_id={order_id} '
            f'{type(exc).__name__}: {exc}'
        )
        return {
            'success': False,
            'mock': False,
            'platform': platform,
            'error': f'{type(exc).__name__}: {exc}',
        }


if __name__ == '__main__':
    mcp.run()
