import json
import os
from typing import Any

import httpx
import streamlit as st


API_BASE_URL = os.getenv('AGENT_API_BASE_URL', 'http://127.0.0.1:8000').rstrip('/')
REQUEST_TIMEOUT = 60
USER_OPTIONS = ['user_001', 'user_003']
JSON_HEADERS = {
    'Accept': 'application/json',
    'Content-Type': 'application/json; charset=utf-8',
}
STREAM_HEADERS = {
    'Accept': 'text/event-stream',
    'Content-Type': 'application/json; charset=utf-8',
}

st.set_page_config(
    page_title='电商客服 Agent',
    page_icon='💬',
    layout='centered',
)


def _init_session_state() -> None:
    defaults: dict[str, Any] = {
        'user_id': USER_OPTIONS[0],
        'messages': [],
        'thread_id': None,
        'pending_approval': None,
        'flash_error': None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _reset_conversation() -> None:
    st.session_state.messages = []
    st.session_state.thread_id = None
    st.session_state.pending_approval = None
    st.session_state.flash_error = None


def _post_json(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
        response = client.post(
            f'{API_BASE_URL}{path}',
            content=body,
            headers=JSON_HEADERS,
        )
        if response.status_code >= 400:
            try:
                detail = response.json().get('detail')
            except ValueError:
                detail = response.text
            raise RuntimeError(
                detail or f'后端返回 HTTP {response.status_code}'
            )
        return response.json()


def _append_message(role: str, content: str) -> None:
    st.session_state.messages.append({'role': role, 'content': content})


def _amount_text(amount: Any) -> str:
    if isinstance(amount, (int, float)) and not isinstance(amount, bool):
        return f'{amount:.2f} 元'
    return '未知'


def _consume_sse_event(
    event: str,
    raw_data: str,
    outcome: dict[str, Any],
    streamed_text: str,
) -> tuple[str, str, bool]:
    if not raw_data:
        return streamed_text, '', False

    try:
        payload = json.loads(raw_data)
    except json.JSONDecodeError as exc:
        outcome['error'] = f'无法解析流式响应：{exc}'
        return streamed_text, '', True
    if not isinstance(payload, dict):
        outcome['error'] = '流式响应格式错误'
        return streamed_text, '', True

    payload_type = payload.get('type')
    if isinstance(payload_type, str) and payload_type:
        event = payload_type

    if event == 'token':
        text = payload.get('text')
        if not isinstance(text, str) or not text:
            return streamed_text, '', False
        streamed_text += text
        return streamed_text, text, False

    if event == 'final':
        outcome['final'] = payload
        reply = payload.get('reply') or ''
        if not isinstance(reply, str):
            reply = str(reply)
        if reply.startswith(streamed_text):
            suffix = reply[len(streamed_text):]
            return streamed_text + suffix, suffix, False
        if not streamed_text:
            return reply, reply, False
        if reply != streamed_text:
            suffix = '\n\n' + reply
            return streamed_text + suffix, suffix, False
        return streamed_text, '', False

    if event == 'error':
        outcome['error'] = str(payload.get('detail') or '流式响应失败')
        return streamed_text, '', True

    return streamed_text, '', False


def _iter_sse_text(
    response: httpx.Response,
    outcome: dict[str, Any],
):
    event_name = 'message'
    data_lines: list[str] = []
    streamed_text = ''
    try:
        for raw_line in response.iter_lines():
            line = raw_line or ''

            if line == '':
                if data_lines:
                    streamed_text, text, stop = _consume_sse_event(
                        event_name,
                        '\n'.join(data_lines),
                        outcome,
                        streamed_text,
                    )
                    outcome['streamed_text'] = streamed_text
                    if text:
                        yield text
                    if stop:
                        return
                event_name = 'message'
                data_lines = []
                continue

            if line.startswith(':'):
                continue
            if line.startswith('event:'):
                event_name = line[6:].strip()
            elif line.startswith('data:'):
                data_lines.append(line[5:].lstrip())

        if data_lines:
            streamed_text, text, _ = _consume_sse_event(
                event_name,
                '\n'.join(data_lines),
                outcome,
                streamed_text,
            )
            outcome['streamed_text'] = streamed_text
            if text:
                yield text
    finally:
        response.close()
        outcome['streamed_text'] = streamed_text
        if not outcome.get('final') and not outcome.get('error'):
            outcome['error'] = '流式响应意外结束'


def _handle_chat_message(message: str) -> None:
    user_id = st.session_state.user_id
    _append_message('user', message)
    with st.chat_message('user'):
        st.markdown(message)

    payload = {
        'message': message,
        'user_id': user_id,
        'thread_id': st.session_state.thread_id,
    }
    outcome: dict[str, Any] = {}
    try:
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        timeout = httpx.Timeout(REQUEST_TIMEOUT, read=REQUEST_TIMEOUT)
        with httpx.Client(timeout=timeout) as client:
            with client.stream(
                'POST',
                f'{API_BASE_URL}/chat_stream',
                content=body,
                headers=STREAM_HEADERS,
            ) as response:
                if response.status_code >= 400:
                    response.read()
                    try:
                        detail = response.json().get('detail')
                    except ValueError:
                        detail = response.text
                    raise RuntimeError(
                        detail or f'后端返回 HTTP {response.status_code}'
                    )

                with st.chat_message('assistant'):
                    st.write_stream(_iter_sse_text(response, outcome))
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        st.session_state.flash_error = f'请求失败：{exc}'
        st.rerun()

    final = outcome.get('final')
    if outcome.get('error') or not isinstance(final, dict):
        partial_reply = outcome.get('streamed_text')
        if isinstance(partial_reply, str) and partial_reply:
            _append_message('assistant', partial_reply)
        error_detail = outcome.get('error') or '未收到最终状态'
        st.session_state.flash_error = '流式请求失败：' + str(error_detail)
        st.rerun()

    st.session_state.thread_id = final.get('thread_id')
    reply = final.get('reply') or outcome.get('streamed_text') or (
        '抱歉，暂时没有生成回复。'
    )
    _append_message('assistant', reply)

    if final.get('need_human_intervention'):
        st.session_state.pending_approval = {
            'thread_id': final.get('thread_id'),
            'user_id': final.get('user_id') or user_id,
            'order_id': final.get('order_id'),
            'order_amount': final.get('order_amount'),
        }
        st.rerun()
    else:
        st.session_state.pending_approval = None


def _handle_approval(decision: str) -> None:
    pending = st.session_state.pending_approval
    if not pending:
        return

    payload = {
        'user_id': pending['user_id'],
        'thread_id': pending['thread_id'],
        'decision': decision,
    }
    try:
        with st.spinner('正在提交审批结果...'):
            data = _post_json('/approve', payload)
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        st.error(f'审批失败：{exc}')
        return

    _append_message('assistant', data.get('reply') or '审批结果已提交。')
    st.session_state.pending_approval = None
    st.rerun()


_init_session_state()

with st.sidebar:
    st.header('客服会话')
    st.selectbox(
        '当前登录用户',
        USER_OPTIONS,
        key='user_id',
        on_change=_reset_conversation,
    )
    st.caption(f'后端服务：{API_BASE_URL}')
    if st.session_state.thread_id:
        st.caption(f'当前会话：{st.session_state.thread_id}')
    if st.button('清空会话', use_container_width=True):
        _reset_conversation()
        st.rerun()

st.title('💬 电商客服')
st.caption('可以咨询商品、订单、使用方法或申请退款。')

for message in st.session_state.messages:
    with st.chat_message(message['role']):
        st.markdown(message['content'])

if st.session_state.flash_error:
    st.error(st.session_state.flash_error)
    st.session_state.flash_error = None

pending = st.session_state.pending_approval
if pending:
    order_id = pending.get('order_id') or '未知订单'
    amount = _amount_text(pending.get('order_amount'))
    st.error(
        '需要人工审批\n\n'
        f'订单号：{order_id}  \n'
        f'退款金额：{amount}'
    )

    approve_column, reject_column = st.columns(2)
    if approve_column.button(
        '同意退款',
        type='primary',
        use_container_width=True,
    ):
        _handle_approval('approve')
    if reject_column.button(
        '拒绝退款',
        use_container_width=True,
    ):
        _handle_approval('reject')
    st.caption('请先完成审批，审批结束后可继续对话。')
else:
    prompt = st.chat_input('请输入您的问题...')
    if prompt and prompt.strip():
        _handle_chat_message(prompt.strip())
