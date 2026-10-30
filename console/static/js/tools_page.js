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
    if (summary) {
        summary.textContent = `${items.length} 个工具 · 直接 ${direct} · 筛选 ${filtered}`
            + (unknown ? ` · 未归类 ${unknown}` : '') + (approval ? ` · 需提权 ${approval}` : '');
    }

    host.innerHTML = items.map((tool) => {
        const modeText = tool.mode === 'direct'
            ? '<span class="tool-badge direct">直接使用</span>'
            : (tool.mode === 'filtered'
                ? `<span class="tool-badge filtered">筛选 · ${toolsEsc(toolsCategoryLabel(tool.category))}</span>`
                : '<span class="tool-badge unknown">未归类</span>');
        const approvalBadge = tool.approval ? '<span class="tool-badge approval">需提权</span>' : '';
        const sourceText = tool.source_group
            ? `<span class="tool-source">${toolsEsc(tool.source_group)}</span>`
            : '';
        return `
            <div class="tool-row" onclick="openToolEditor('${toolsEsc(tool.name)}')">
                <span class="tool-name">${toolsEsc(tool.name)}</span>
                ${sourceText}
                <span class="tool-badges">${modeText}${approvalBadge}</span>
            </div>`;
    }).join('');
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
    if (!modal) return;

    if (title) title.textContent = tool.name;
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

    const needsCategory = tool.mode !== 'direct';
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

    try {
        const resp = await fetch('/api/tools', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: tool.name, mode, approval, category }),
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
