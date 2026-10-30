// -*- coding: utf-8 -*-
// Stellaris - Message center: 只看本次启动后的消息

const messagesState = {
    items: [],
    counts: { info: 0, warn: 0, error: 0 },
    total: 0,
    open: false,
    seenTotal: 0,
};

function msgEsc(value) {
    return typeof escapeHtml === 'function'
        ? escapeHtml(value)
        : String(value == null ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function msgTime(ts) {
    if (!ts) return '';
    const d = new Date(ts * 1000);
    const pad = (n) => String(n).padStart(2, '0');
    return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function renderMessages() {
    const list = document.getElementById('msg-list');
    const meta = document.getElementById('msg-meta');
    const dot = document.getElementById('msg-dot');
    if (!list) return;

    const { items, counts, total } = messagesState;
    if (meta) {
        meta.textContent = total
            ? `${total} 条 · 错误 ${counts.error || 0} · 警告 ${counts.warn || 0}`
            : '本次启动以来没有消息';
    }

    // 未读标记：面板关着且有新消息
    const unread = !messagesState.open && total > messagesState.seenTotal;
    if (dot) dot.style.display = unread ? '' : 'none';

    if (!items.length) {
        list.innerHTML = '<div class="msg-empty">本次启动以来没有消息。</div>';
        return;
    }

    list.innerHTML = items.slice().reverse().map((m) => {
        const level = ['info', 'warn', 'error'].includes(m.level) ? m.level : 'info';
        return `
            <div class="msg-row">
                <span class="msg-dot-${level}"></span>
                <span class="msg-time">${msgEsc(msgTime(m.ts))}</span>
                <span class="msg-text">${msgEsc(m.text)}</span>
            </div>`;
    }).join('');
}

async function loadMessages() {
    try {
        const resp = await fetch('/api/messages');
        const data = await resp.json();
        if (!data.ok) return;
        messagesState.items = Array.isArray(data.messages) ? data.messages : [];
        messagesState.counts = data.counts || { info: 0, warn: 0, error: 0 };
        messagesState.total = Number(data.total) || 0;
        renderMessages();
    } catch (e) {
        // 静默：消息中心不是关键路径
    }
}

function toggleMessages(force) {
    const panel = document.getElementById('msg-panel');
    if (!panel) return;
    const next = (typeof force === 'boolean') ? force : panel.style.display === 'none';
    panel.style.display = next ? 'block' : 'none';
    messagesState.open = next;
    if (next) {
        messagesState.seenTotal = messagesState.total;
        renderMessages();
        loadMessages();
    } else {
        renderMessages();
    }
}

// 点面板外面关闭
document.addEventListener('click', (event) => {
    const panel = document.getElementById('msg-panel');
    const bell = document.getElementById('msg-bell');
    if (!panel || panel.style.display === 'none') return;
    if (panel.contains(event.target) || (bell && bell.contains(event.target))) return;
    toggleMessages(false);
});

document.addEventListener('DOMContentLoaded', () => {
    loadMessages();
    setInterval(loadMessages, 15000);
    // 方便直达：/#messages 自动打开弹窗
    if (window.location.hash === '#messages') {
        setTimeout(() => toggleMessages(true), 400);
    }
});

window.toggleMessages = toggleMessages;
window.loadMessages = loadMessages;
