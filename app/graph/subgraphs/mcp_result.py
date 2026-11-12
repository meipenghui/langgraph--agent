import json
from typing import Any


def extract_payload(result: Any) -> Any:
    if isinstance(result, tuple):
        content, artifact = result
        if isinstance(artifact, dict) and artifact.get('structured_content') is not None:
            return artifact['structured_content']
        result = content

    if hasattr(result, 'content') and not isinstance(result, (str, list, dict)):
        result = result.content

    if isinstance(result, str):
        try:
            return json.loads(result)
        except json.JSONDecodeError:
            return result

    if isinstance(result, list):
        text_parts = [
            block['text']
            for block in result
            if isinstance(block, dict)
            and block.get('type') == 'text'
            and isinstance(block.get('text'), str)
        ]
        if text_parts:
            text = '\n'.join(text_parts)
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text

    return result


def extract_first_order(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None

    orders = payload.get('orders')
    if not isinstance(orders, list) or not orders:
        return None

    first_order = orders[0]
    return first_order if isinstance(first_order, dict) else None


def extract_order_id(payload: Any) -> str | None:
    order = extract_first_order(payload)
    if order is None:
        return None

    order_id = order.get('order_id')
    return order_id if isinstance(order_id, str) else None


def extract_order_amount(payload: Any) -> float | None:
    order = extract_first_order(payload)
    if order is None:
        return None

    amount = order.get('amount')
    if isinstance(amount, bool) or not isinstance(amount, (int, float)):
        return None
    return float(amount)
