// -*- coding: utf-8 -*-
// Stellaris - Runtime logs + top alert badge

const logsState = { loaded: false };

function logsEsc(value) {
    return typeof escapeHtml === 'function'
        ? escapeHtml(value)
        : String(value == null ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

async function loadLogs() {
    const view = document.getElementById('logs-view');
    const meta = document.getElementById('logs-meta');
    if (!view) return;

    const onlyError = !!document.getElementById('logs-only-error')?.checked;
    const includePrev = !!document.getElementById('logs-include-prev')?.checked;
    const tail = document.getElementById('logs-tail')?.value || '500';
    view.textContent = '加载中…';

    try {
        const params = new URLSearchParams({ tail, filter: onlyError ? 'error' : 'all' });
        if (includePrev) params.set('include_prev', '1');
        const resp = await fetch(`/api/logs?${params.toString()}`);
        const data = await resp.json();
        if (!data.ok) {
            view.textContent = '读取失败：' + (data.error || '未知错误');
            return;
        }
        const blocks = [];
        let total = 0;
        for (const file of data.files || []) {
            if (file.error) {
                blocks.push(`===== ${file.name} =====\n[读取失败] ${file.error}`);
                continue;
            }
            const lines = file.lines || [];
            total += lines.length;
            const sizeKb = (file.size / 1024).toFixed(1);
            blocks.push(`===== ${file.name}  (${sizeKb} KB) =====\n` + (lines.join('\n') || '（空）'));
        }
        view.textContent = blocks.join('\n\n') || '（没有日志文件）';
        if (meta) meta.textContent = `${data.files?.length || 0} 个文件 · ${total} 行`;
        logsState.loaded = true;
        view.scrollTop = view.scrollHeight;
    } catch (e) {
        view.textContent = '读取失败：' + (e.message || e);
    }
}

function openLogsTab() {
    if (typeof openSettingsTab === 'function') openSettingsTab(document.getElementById('footer-settings-btn'));
    const btn = document.querySelector("#tab-settings .subtab-btn[onclick*='subtab-logs']");
    if (typeof switchSettingsSubtab === 'function') switchSettingsSubtab('subtab-logs', btn);
    loadLogs();
}

document.addEventListener('DOMContentLoaded', () => {
    setInterval(refreshAlerts, 20000);
});

window.loadLogs = loadLogs;
window.openLogsTab = openLogsTab;
