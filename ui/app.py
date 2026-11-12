import json
import os
from typing import Any

import requests
import streamlit as st


API_BASE_URL = os.getenv('AGENT_API_BASE_URL', 'http://127.0.0.1:8000').rstrip('/')
REQUEST_TIMEOUT = 60
USER_OPTIONS = ['user_001', 'user_003']

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
    response = requests.post(
        f'{API_BASE_URL}{path}',
        data=body,
        headers={
            'Accept': 'application/json',
            'Content-Type': 'application/json; charset=utf-8',
        },
        timeout=REQUEST_TIMEOUT,
    )
    response.encoding = 'utf-8'
    if not response.ok:
        try:
            detail = response.json().get('detail')
        except ValueError:
            detail = response.text
        raise RuntimeError(detail or f'后端返回 HTTP {response.status_code}')
    return response.json()


def _append_message(role: str, content: str) -> None:
    st.session_state.messages.append({'role': role, 'content': content})


def _amount_text(amount: Any) -> str:
    if isinstance(amount, (int, float)) and not isinstance(amount, bool):
        return f'{amount:.2f} 元'
    return '未知'


def _handle_chat_message(message: str) -> None:
    user_id = st.session_state.user_id
    _append_message('user', message)

    payload = {
        'message': message,
        'user_id': user_id,
        'thread_id': st.session_state.thread_id,
    }
    try:
        with st.spinner('客服正在处理...'):
            data = _post_json('/chat', payload)
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        st.session_state.flash_error = f'请求失败：{exc}'
        st.rerun()

    st.session_state.thread_id = data.get('thread_id')
    _append_message('assistant', data.get('reply') or '抱歉，暂时没有生成回复。')

    if data.get('need_human_intervention'):
        st.session_state.pending_approval = {
            'thread_id': data.get('thread_id'),
            'user_id': data.get('user_id') or user_id,
            'order_id': data.get('order_id'),
            'order_amount': data.get('order_amount'),
        }
    else:
        st.session_state.pending_approval = None

    st.rerun()


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
    except (requests.RequestException, RuntimeError, ValueError) as exc:
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
