import os
from typing import Any
from unittest.mock import patch

import httpx

import mcp_server.server as server_module
from app.core.config import load_im_settings


def fake_post(
    captured: list[tuple[str, dict[str, Any]]],
    response_body: dict[str, Any],
):
    def _post(
        url: str,
        json: dict[str, Any],
        timeout: float,
    ) -> httpx.Response:
        assert timeout == 10.0
        captured.append((url, json))
        return httpx.Response(
            200,
            json=response_body,
            request=httpx.Request('POST', url),
        )

    return _post


def run_im_notification_test() -> None:
    with patch.dict(os.environ, {'IM_WEBHOOK_URL': ''}, clear=False):
        assert load_im_settings().webhook_url is None
        mock_result = server_module.send_im_notification(
            user_id='user_003',
            amount=699.0,
            order_id='TB202611120003',
            reason='用户申请退款',
        )
        assert mock_result['success'] is True
        assert mock_result['mock'] is True
        assert '退款人工审批提醒' in mock_result['message']

    feishu_calls: list[tuple[str, dict[str, Any]]] = []
    feishu_url = 'https://open.feishu.cn/open-apis/bot/v2/hook/test-token'
    with patch.object(
        server_module.httpx,
        'post',
        fake_post(feishu_calls, {'code': 0}),
    ):
        feishu_result = server_module.send_im_notification(
            user_id='user_003',
            amount=699.0,
            order_id='TB202611120003',
            reason='用户申请退款',
            webhook_url=feishu_url,
        )
    assert feishu_result['success'] is True
    assert feishu_result['platform'] == 'feishu'
    assert feishu_calls[0][1]['msg_type'] == 'text'
    assert '退款人工审批提醒' in feishu_calls[0][1]['content']['text']

    dingtalk_calls: list[tuple[str, dict[str, Any]]] = []
    dingtalk_url = 'https://oapi.dingtalk.com/robot/send?access_token=test-token'
    with patch.object(
        server_module.httpx,
        'post',
        fake_post(dingtalk_calls, {'errcode': 0}),
    ):
        dingtalk_result = server_module.send_im_notification(
            user_id='user_003',
            amount=699.0,
            order_id='TB202611120003',
            reason='用户申请退款',
            webhook_url=dingtalk_url,
        )
    assert dingtalk_result['success'] is True
    assert dingtalk_result['platform'] == 'dingtalk'
    assert dingtalk_calls[0][1]['msgtype'] == 'markdown'
    assert '退款人工审批提醒' in dingtalk_calls[0][1]['markdown']['text']

    error_calls: list[tuple[str, dict[str, Any]]] = []
    with patch.object(
        server_module.httpx,
        'post',
        fake_post(error_calls, {'errcode': 310000, 'errmsg': 'invalid token'}),
    ):
        error_result = server_module.send_im_notification(
            user_id='user_003',
            amount=699.0,
            order_id='TB202611120003',
            reason='用户申请退款',
            webhook_url=dingtalk_url,
        )
    assert error_result['success'] is False
    assert '310000' in error_result['error']

    print('IM_NOTIFICATION_OK')


if __name__ == '__main__':
    run_im_notification_test()
