// -*- coding: utf-8 -*-
// Stellaris - Runtime logs + top alert badge

const logsState = { loaded: false };

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

async function loadAudit() {
    const view = document.getElementById('logs-view');
    const meta = document.getElementById('logs-meta');
    if (!view) return;
    view.textContent = '加载中…';
    try {
        const resp = await fetch('/api/audit?limit=500', { cache: 'no-store' });
        const data = await resp.json();
        if (!data.ok) {
            view.textContent = '读取失败：' + (data.error || '未知错误');
            return;
        }
        const entries = data.entries || [];
        const lines = entries.map(function (item) {
            const bits = [item.time || '', '[' + (item.level || 'info') + ']', item.event || ''];
            ['ip', 'username', 'path', 'method', 'error', 'retry_after', 'local_only',
             'signature_enabled', 'grace_seconds'].forEach(function (key) {
                if (item[key] !== undefined && item[key] !== '' && item[key] !== null) {
                    bits.push(key + '=' + item[key]);
                }
            });
            return bits.join(' ');
        });
        view.textContent = lines.join('\n') || '（暂无安全事件）';
        if (meta) {
            const info = data.stats || {};
            const mb = ((info.bytes || 0) / 1024 / 1024).toFixed(1);
            meta.textContent = `${entries.length} 条 · ${info.files || 0} 个文件 · ${mb} MB`
                + (info.retention_days ? ` · 保留 ${info.retention_days} 天` : '');
        }
        view.scrollTop = 0;
    } catch (e) {
        view.textContent = '读取失败：' + (e.message || e);
    }
}

async function loadDeployCheck() {
    const view = document.getElementById('logs-view');
    const meta = document.getElementById('logs-meta');
    if (!view) return;
    view.textContent = '检查中…（docker 探测可能要几秒）';
    try {
        const resp = await fetch('/api/deploy_check', { cache: 'no-store' });
        const data = await resp.json();
        if (!data.ok) {
            view.textContent = '检查失败：' + (data.error || '未知错误');
            return;
        }
        view.textContent = (data.checks || []).map(function (item) {
            const icon = item.status === 'ok' ? '✓' : (item.status === 'warn' ? '!' : '✗');
            return icon + ' ' + item.name + '  ' + item.detail;
        }).join('\n') || '（没有结果）';
        const s = data.summary || {};
        if (meta) {
            meta.textContent = `${s.total || 0} 项 · ${s.ok || 0} 通过 / ${s.warn || 0} 警告 / ${s.fail || 0} 失败`;
        }
    } catch (e) {
        view.textContent = '检查失败：' + (e.message || e);
    }
}

function openLogsTab() {
    if (typeof openSettingsTab === 'function') openSettingsTab(document.getElementById('footer-settings-btn'));
    const btn = document.querySelector("#tab-settings .subtab-btn[onclick*='subtab-logs']");
    if (typeof switchSettingsSubtab === 'function') switchSettingsSubtab('subtab-logs', btn);
    loadLogs();
}

// 日志页只在打开时按需加载，不再挂一个指向未定义函数的定时器（审计 B29）。
window.loadLogs = loadLogs;
window.loadAudit = loadAudit;
window.loadDeployCheck = loadDeployCheck;
window.openLogsTab = openLogsTab;
