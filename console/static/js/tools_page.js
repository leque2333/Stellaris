// -*- coding: utf-8 -*-
// Stellaris - 工具页：主模型直用 vs 执行模型筛选，以及提权标记

const toolsState = {
    items: [],
    categories: [],
    current: null,
    loaded: false,
};

function toolsEsc(value) {
    return typeof escapeHtml === 'function'
        ? escapeHtml(value)
        : String(value == null ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function toolsCategoryLabel(name) {
    const hit = toolsState.categories.find((c) => c.name === name);
    return hit ? (hit.label || hit.name) : (name || '未分类');
}

function renderToolsList() {
    const host = document.getElementById('tools-list');
    const summary = document.getElementById('tools-summary');
    if (!host) return;

    const items = toolsState.items;
    if (!items.length) {
        host.innerHTML = '<div class="archive-empty">还没有工具。等网关收到一次带工具的请求后就会出现在这里。</div>';
        if (summary) summary.textContent = '';
        return;
    }

    const direct = items.filter((t) => t.mode === 'direct').length;
    const filtered = items.filter((t) => t.mode === 'filtered').length;
    const unknown = items.filter((t) => t.mode === 'unknown').length;
    const approval = items.filter((t) => t.approval).length;
    const system = items.filter((t) => t.system).length;
    if (summary) {
        summary.textContent = `${items.length} 个工具 · 直接 ${direct} · 筛选 ${filtered}`
            + (system ? ` · 系统 ${system}` : '')
            + (unknown ? ` · 未归类 ${unknown}` : '') + (approval ? ` · 需提权 ${approval}` : '');
    }

    const groups = new Map();
    items.forEach((tool) => {
        const key = tool.system ? '系统工具' : (tool.source_group || '未分组');
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(tool);
    });
    const order = [...groups.keys()].sort((a, b) => {
        if (a === '系统工具') return -1;
        if (b === '系统工具') return 1;
        return a.localeCompare(b, 'zh-CN');
    });

    host.innerHTML = order.map((key) => {
        const rows = toolsState.items.length
            ? groups.get(key).map((tool) => renderToolRow(tool, items.indexOf(tool))).join('')
            : '';
        const open = key === '系统工具' ? ' open' : '';
        return `
            <details class="tool-group"${open}>
                <summary class="tool-group-head">
                    <span>${toolsEsc(key)}</span>
                    <span class="tool-group-count">${groups.get(key).length}</span>
                </summary>
                <div class="tool-group-body">${rows}</div>
            </details>`;
    }).join('');
    bindToolListClicks(host);
}

function bindToolListClicks(host) {
    // 内联 onclick 会把工具名拼进 HTML 属性，等于把注入点交给任何客户端 Key
    if (host.dataset.clickBound === '1') return;
    host.dataset.clickBound = '1';
    host.addEventListener('click', (event) => {
        const row = event.target.closest ? event.target.closest('.tool-row') : null;
        if (!row || !host.contains(row)) return;
        const tool = toolsState.items[Number(row.dataset.toolIndex)];
        if (tool) openToolEditor(tool.name);
    });
}

function renderToolRow(tool, index) {
    const modeText = tool.mode === 'direct'
        ? '<span class="tool-badge direct">直接使用</span>'
        : (tool.mode === 'filtered'
            ? `<span class="tool-badge filtered">筛选 · ${toolsEsc(toolsCategoryLabel(tool.category))}</span>`
            : '<span class="tool-badge unknown">未归类</span>');
    const approvalBadge = tool.approval ? '<span class="tool-badge approval">需提权</span>' : '';
    const systemBadge = tool.system
        ? `<span class="tool-badge system">系统工具${tool.enabled === false ? ' · 已停用' : ''}</span>`
        : '';
    // 行内不拼任何用户可控字符串：只传下标，工具名统一走 textContent / escapeHtml
    return `
        <div class="tool-row" data-tool-index="${index}">
            <span class="tool-name">${toolsEsc(tool.name)}</span>
            <span class="tool-badges">${modeText}${systemBadge}${approvalBadge}</span>
        </div>`;
}

async function loadToolsPage() {
    const host = document.getElementById('tools-list');
    if (host) host.innerHTML = '<div class="archive-empty">正在加载…</div>';
    try {
        const resp = await fetch('/api/tools');
        const data = await resp.json();
        if (!data.ok) {
            if (host) host.innerHTML = `<div class="archive-empty">加载失败：${toolsEsc(data.error || '')}</div>`;
            return;
        }
        toolsState.items = Array.isArray(data.tools) ? data.tools : [];
        toolsState.categories = Array.isArray(data.categories) ? data.categories : [];
        toolsState.loaded = true;
        renderToolsList();
        if (window.location.hash === '#tools-edit' && toolsState.items.length) {
            openToolEditor(toolsState.items[0].name);
        }
    } catch (e) {
        if (host) host.innerHTML = `<div class="archive-empty">加载失败：${toolsEsc(e.message || e)}</div>`;
    }
}

function openToolEditor(name) {
    const tool = toolsState.items.find((t) => t.name === name);
    if (!tool) return;
    toolsState.current = tool;

    const modal = document.getElementById('tool-modal');
    const title = document.getElementById('tool-modal-name');
    const approval = document.getElementById('tool-approval');
    const categoryRow = document.getElementById('tool-category-row');
    const categorySelect = document.getElementById('tool-category');
    const modeRow = document.getElementById('tool-mode-row');
    const systemRow = document.getElementById('tool-system-row');
    const permissionRow = document.getElementById('tool-permission-row');
    const enabledBox = document.getElementById('tool-enabled');
    const systemNote = document.getElementById('tool-system-note');
    if (!modal) return;

    if (title) title.textContent = tool.name;
    const isSystem = !!tool.system;
    if (modeRow) modeRow.style.display = isSystem ? 'none' : '';
    if (permissionRow) permissionRow.style.display = isSystem ? 'none' : '';
    if (systemRow) systemRow.style.display = isSystem ? '' : 'none';
    if (enabledBox) enabledBox.checked = tool.enabled !== false;
    if (systemNote) {
        systemNote.textContent = isSystem
            ? `${tool.system_label || tool.name} · ${tool.transport || 'system'}`
            : '';
    }
    document.querySelectorAll('input[name="tool-mode"]').forEach((input) => {
        input.checked = input.value === (tool.mode === 'direct' ? 'direct' : 'filtered');
    });
    if (approval) approval.checked = !!tool.approval;

    if (categorySelect) {
        categorySelect.innerHTML = toolsState.categories
            .map((c) => `<option value="${toolsEsc(c.name)}">${toolsEsc(c.label || c.name)}</option>`)
            .join('');
        if (tool.category) categorySelect.value = tool.category;
    }

    const needsCategory = !isSystem && tool.mode !== 'direct';
    if (categoryRow) categoryRow.style.display = needsCategory ? 'flex' : 'none';

    modal.classList.add('show');
}

function closeToolEditor() {
    document.getElementById('tool-modal')?.classList.remove('show');
    toolsState.current = null;
}

function onToolModeChange() {
    const mode = document.querySelector('input[name="tool-mode"]:checked')?.value || 'direct';
    const row = document.getElementById('tool-category-row');
    if (row) row.style.display = mode === 'filtered' ? 'flex' : 'none';
}

async function saveToolEditor() {
    const tool = toolsState.current;
    if (!tool) return;
    const mode = document.querySelector('input[name="tool-mode"]:checked')?.value || 'direct';
    const approval = !!document.getElementById('tool-approval')?.checked;
    const category = document.getElementById('tool-category')?.value || '';
    const enabled = !!document.getElementById('tool-enabled')?.checked;

    try {
        const body = tool.system
            ? { name: tool.name, enabled }
            : { name: tool.name, mode, approval, category };
        const resp = await fetch('/api/tools', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok || !data.ok) {
            if (typeof showToast === 'function') showToast(data.error || '保存失败');
            return;
        }
        if (typeof showToast === 'function') showToast(`${tool.name} 已更新`);
        closeToolEditor();
        await loadToolsPage();
    } catch (e) {
        if (typeof showToast === 'function') showToast('保存失败：' + (e.message || e));
    }
}

// ---------------- 插件工具扫描（设置 → 常规与系统） ----------------
function pluginScanFrameworkLabel(key) {
    const map = {
        astrbot: 'AstrBot',
        nonebot: 'NoneBot',
        langbot: 'LangBot',
        dify: 'Dify',
        generic: '通用',
    };
    return map[key] || key || '通用';
}

function renderPluginScanStatus(data) {
    const statusEl = document.getElementById('plugin-scan-status');
    const pathsEl = document.getElementById('plugin-scan-paths');
    const invalid = Array.isArray(data.invalid) ? data.invalid : [];
    if (statusEl) {
        const parts = [`已识别 ${data.plugin_count || 0} 个插件 / ${data.tool_count || 0} 个工具`];
        if (data.auto) parts.push('自动识别已开启');
        if (invalid.length) parts.push(`忽略 ${invalid.length} 个不存在的路径`);
        statusEl.innerText = parts.join(' · ');
        statusEl.style.color = invalid.length ? 'var(--accent-amber, #d97706)' : 'var(--text-muted)';
    }
    if (!pathsEl) return;
    const roots = Array.isArray(data.resolved_roots) ? data.resolved_roots : [];
    const plugins = Array.isArray(data.plugins) ? data.plugins : [];
    const lines = [];
    roots.slice(0, 10).forEach((root) => {
        lines.push(`<div>目录：<code>${toolsEsc(root)}</code></div>`);
    });
    plugins.slice(0, 30).forEach((plugin) => {
        const tools = Array.isArray(plugin.tools) ? plugin.tools : [];
        lines.push(
            `<div>• <b>${toolsEsc(plugin.name)}</b> `
            + `<span style="opacity:.7">${toolsEsc(pluginScanFrameworkLabel(plugin.framework))} · ${tools.length} 个工具</span> `
            + `<code>${toolsEsc(plugin.path || '')}</code></div>`
        );
    });
    if (!roots.length) {
        lines.push('<div>没有解析到可用目录；填一个插件目录，或勾选自动识别。</div>');
    }
    if (plugins.length > 30) {
        lines.push(`<div>… 还有 ${plugins.length - 30} 个插件</div>`);
    }
    pathsEl.innerHTML = lines.join('');
}

async function loadPluginScanConfig() {
    const rootsEl = document.getElementById('plugin-scan-roots');
    if (!rootsEl) return;
    try {
        const resp = await fetch('/api/tools/plugin_scan', { cache: 'no-store' });
        if (!resp.ok) return;
        const data = await resp.json();
        rootsEl.value = (data.roots || []).join('\n');
        const autoEl = document.getElementById('plugin-scan-auto');
        if (autoEl) autoEl.checked = data.auto !== false;
        renderPluginScanStatus(data);
    } catch (e) {
        console.warn('plugin scan load failed:', e);
    }
}

async function savePluginScanConfig() {
    const rootsEl = document.getElementById('plugin-scan-roots');
    const autoEl = document.getElementById('plugin-scan-auto');
    const roots = (rootsEl?.value || '')
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter(Boolean);
    try {
        const resp = await fetch('/api/tools/plugin_scan', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ roots, auto: !!autoEl?.checked }),
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok || data.ok === false) {
            if (typeof showToast === 'function') showToast(data.error || '保存失败');
            return;
        }
        if (rootsEl) rootsEl.value = (data.roots || []).join('\n');
        renderPluginScanStatus(data);
        if (typeof showToast === 'function') {
            showToast(`插件目录已保存：${data.plugin_count || 0} 个插件 / ${data.tool_count || 0} 个工具`);
        }
        if (typeof loadToolsPage === 'function') loadToolsPage();
    } catch (e) {
        if (typeof showToast === 'function') showToast('保存失败：' + (e.message || e));
    }
}

document.addEventListener('DOMContentLoaded', () => {
    const tab = document.getElementById('tab-tools');
    if (tab && tab.classList.contains('active')) {
        loadToolsPage().then(() => {
            if (window.location.hash === '#tools-edit' && toolsState.items.length) {
                openToolEditor(toolsState.items[0].name);
            }
        });
    }
});

window.loadToolsPage = loadToolsPage;
window.openToolEditor = openToolEditor;
window.closeToolEditor = closeToolEditor;
window.onToolModeChange = onToolModeChange;
window.saveToolEditor = saveToolEditor;
window.loadPluginScanConfig = loadPluginScanConfig;
window.savePluginScanConfig = savePluginScanConfig;
