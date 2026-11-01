// -*- coding: utf-8 -*-
// Stellaris - 工具页：主模型直用 vs 执行模型筛选，以及提权标记

const toolsState = {
    items: [],
    categories: [],
    current: null,
    loaded: false,
    policies: {},
    archives: [],
    target: '__global__',
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
        const rows = groups.get(key).map(renderToolRow).join('');
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
}

function renderToolRow(tool) {
    const modeText = tool.mode === 'direct'
        ? '<span class="tool-badge direct">直接使用</span>'
        : (tool.mode === 'filtered'
            ? `<span class="tool-badge filtered">筛选 · ${toolsEsc(toolsCategoryLabel(tool.category))}</span>`
            : '<span class="tool-badge unknown">未归类</span>');
    const approvalBadge = tool.approval ? '<span class="tool-badge approval">需提权</span>' : '';
    const systemBadge = tool.system
        ? `<span class="tool-badge system">系统工具${tool.enabled === false ? ' · 已停用' : ''}</span>`
        : '';
    return `
        <div class="tool-row" onclick="openToolEditor('${toolsEsc(tool.name)}')">
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

// ---------------- 档案级工具权限 ----------------
const POLICY_GUEST = '__guest__';

function onToolsTargetChange(value) {
    toolsState.target = value || '__global__';
    renderPolicyPanel();
}

function renderTargets() {
    const sel = document.getElementById('tools-policy-target');
    if (!sel) return;
    const current = toolsState.target || '__global__';
    const opts = ['<option value="__global__">全局配置（所有档案共用）</option>'];
    const policies = toolsState.policies || {};
    (toolsState.archives || []).forEach(function (a) {
        const id = a.archive_id || '';
        if (!id) return;
        const name = a.display_name || id;
        const mark = policies[id] ? ' · 已配置' : '';
        opts.push(`<option value="${toolsEsc(id)}">${toolsEsc(name)}（${toolsEsc(id)}）${mark}</option>`);
    });
    const guestMark = policies[POLICY_GUEST] ? ' · 已配置' : '';
    opts.push(`<option value="${POLICY_GUEST}">陌生访客（无档案）${guestMark}</option>`);
    sel.innerHTML = opts.join('');
    sel.value = current;
}

function renderPolicyPanel() {
    const panel = document.getElementById('tools-policy-panel');
    const list = document.getElementById('tools-list');
    const summary = document.getElementById('tools-summary');
    if (!panel) return;
    const target = toolsState.target || '__global__';

    if (target === '__global__') {
        panel.style.display = 'none';
        panel.innerHTML = '';
        if (list) list.style.display = '';
        if (summary) summary.style.display = '';
        return;
    }

    if (list) list.style.display = 'none';
    if (summary) summary.style.display = 'none';
    panel.style.display = '';

    const policy = (toolsState.policies || {})[target] || { mode: 'all', tools: [] };
    const mode = policy.mode || 'all';
    const selected = new Set(policy.tools || []);
    const label = target === POLICY_GUEST
        ? '陌生访客（无档案）'
        : ((toolsState.archives || []).find((a) => a.archive_id === target) || {}).display_name || target;

    const rows = (toolsState.items || []).map(function (tool) {
        const checked = selected.has(tool.name) ? 'checked' : '';
        return `
            <label class="policy-tool-row">
                <input type="checkbox" value="${toolsEsc(tool.name)}" ${checked} />
                <span class="tool-name">${toolsEsc(tool.name)}</span>
                <span class="tool-source">${toolsEsc(tool.source_group || '')}</span>
            </label>`;
    }).join('');

    panel.innerHTML = `
        <div class="policy-head">
            <div class="policy-title">${toolsEsc(label)} 的工具权限</div>
            <div class="policy-hint">留空并选「全部工具」= 该档案不受限；白名单 = 只给勾选的；黑名单 = 除了勾选的全给。</div>
        </div>
        <div class="policy-mode-row">
            <label class="tool-choice"><input type="radio" name="policy-mode" value="all" ${mode === 'all' ? 'checked' : ''} onchange="onPolicyModeChange()" /> <span><b>全部工具</b>　不受限</span></label>
            <label class="tool-choice"><input type="radio" name="policy-mode" value="allow" ${mode === 'allow' ? 'checked' : ''} onchange="onPolicyModeChange()" /> <span><b>白名单</b>　只给勾选的</span></label>
            <label class="tool-choice"><input type="radio" name="policy-mode" value="deny" ${mode === 'deny' ? 'checked' : ''} onchange="onPolicyModeChange()" /> <span><b>黑名单</b>　除了勾选的全给</span></label>
        </div>
        <div class="policy-tools ${mode === 'all' ? 'is-disabled' : ''}" id="policy-tools">${rows || '<div class="archive-empty">还没有工具</div>'}</div>
        <div class="policy-actions">
            <button class="btn btn-secondary" onclick="deletePolicy('${toolsEsc(target)}')">清除该档案配置</button>
            <button class="btn btn-primary" onclick="savePolicy('${toolsEsc(target)}')">保存</button>
        </div>`;
    onPolicyModeChange();
}

function onPolicyModeChange() {
    const mode = (document.querySelector('input[name="policy-mode"]:checked') || {}).value || 'all';
    const box = document.getElementById('policy-tools');
    if (box) {
        box.classList.toggle('is-disabled', mode === 'all');
    }
}

async function savePolicy(target) {
    const mode = (document.querySelector('input[name="policy-mode"]:checked') || {}).value || 'all';
    const tools = Array.from(document.querySelectorAll('#policy-tools input[type="checkbox"]:checked'))
        .map((el) => el.value);
    if (mode !== 'all' && !tools.length) {
        alert('白名单 / 黑名单模式下至少要勾一个工具');
        return;
    }
    try {
        const res = await fetch('/api/tools/policy', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ archive_id: target === POLICY_GUEST ? '' : target, mode: mode, tools: tools }),
        });
        const data = await res.json();
        if (!data.ok) { alert(data.error || '保存失败'); return; }
        await loadPolicies();
        renderPolicyPanel();
    } catch (e) {
        alert('保存失败：' + e.message);
    }
}

async function deletePolicy(target) {
    if (!confirm('清除这个档案的工具配置？')) return;
    try {
        const res = await fetch('/api/tools/policy/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ archive_id: target === POLICY_GUEST ? '' : target }),
        });
        const data = await res.json();
        if (!data.ok) { alert(data.error || '清除失败'); return; }
        await loadPolicies();
        renderPolicyPanel();
    } catch (e) {
        alert('清除失败：' + e.message);
    }
}

async function loadPolicies() {
    try {
        const res = await fetch('/api/tools/policies');
        const data = await res.json();
        toolsState.policies = data.policies || {};
        toolsState.archives = data.archives || [];
        renderTargets();
    } catch (e) {
        toolsState.policies = {};
        toolsState.archives = [];
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
