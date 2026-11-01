// -*- coding: utf-8 -*-
// Stellaris - Identity map: bind scanned UIDs to archives, grouped by plugin

const identityMapState = {
    groups: [],
    archives: [],
    dirty: new Map(),   // key = platform|uid -> { platform, uid, archive_id, display_name }
    unbind: new Map(),  // key = platform|uid -> { platform, uid }（原来是已配置，现在改回不配置）
    loaded: false,
    showHidden: false,
};

function identityEsc(value) {
    return typeof escapeHtml === 'function'
        ? escapeHtml(value)
        : String(value == null ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function identityPlatform(hit) {
    const hint = String(hit.platform_hint || '').trim();
    return (!hint || hint === 'unknown') ? 'qq' : hint;
}

function identityArchiveOptions(selected) {
    const opts = ['<option value="">不配置</option>'];
    for (const a of identityMapState.archives) {
        const label = a.display_name || a.archive_id;
        const mark = a.role === 'admin' ? '（管理员）' : '（访客）';
        const sel = a.archive_id === selected ? ' selected' : '';
        opts.push(`<option value="${identityEsc(a.archive_id)}"${sel}>${identityEsc(label)}${mark}</option>`);
    }
    return opts.join('');
}

function identityFindArchive(archiveId) {
    return identityMapState.archives.find(a => a.archive_id === archiveId) || null;
}

function identityVisibleUids(group) {
    if (!Array.isArray(group.uids)) return [];
    return identityMapState.showHidden
        ? group.uids
        : group.uids.filter(u => !u.hidden);
}

function renderIdentityGroups() {
    const host = document.getElementById('identity-groups');
    const summary = document.getElementById('identity-scan-summary');
    if (!host) return;

    if (!identityMapState.groups.length) {
        host.innerHTML = '<div class="archive-empty">没有扫描到任何 UID。请确认插件已安装，或点「重新扫描」。</div>';
        if (summary) summary.innerText = '';
        return;
    }

    const allUids = identityMapState.groups.reduce((n, g) => n + (g.uids || []).length, 0);
    const configured = identityMapState.groups.reduce((n, g) => n + (g.configured || 0), 0);
    const hiddenCount = identityMapState.groups.reduce(
        (n, g) => n + (g.uids || []).filter(u => u.hidden).length, 0);
    if (summary) {
        summary.innerHTML = `
            <span>${identityMapState.groups.length} 个插件 · ${allUids} 个 UID · 已配置 ${configured} · 待配置 ${allUids - configured}${hiddenCount ? ` · 已隐藏 ${hiddenCount}` : ''}</span>
            ${hiddenCount ? `<label class="id-show-hidden"><input type="checkbox" ${identityMapState.showHidden ? 'checked' : ''} onchange="toggleIdentityShowHidden(this.checked)"> 显示已隐藏 (${hiddenCount})</label>` : ''}`;
    }

    host.innerHTML = identityMapState.groups.map((group, gi) => {
        const visible = identityVisibleUids(group);
        const hiddenInGroup = (group.uids || []).filter(u => u.hidden).length;
        const rows = visible.map((hit) => {
            const platform = identityPlatform(hit);
            const configured = hit.configured && hit.archive_id;
            const active = identityMapState.dirty.get(`${platform}|${hit.uid}`);
            const selected = active ? active.archive_id : (configured ? hit.archive_id : '');
            const src = (hit.sources || [])[0] || {};
            const origin = [src.path ? String(src.path).split(/[\\/]/).pop() : '', src.field || '']
                .filter(Boolean).join(' · ');
            const badge = hit.hidden
                ? `<span class="id-badge muted">已隐藏</span>`
                : (configured ? `<span class="id-badge ok">已配</span>` : `<span class="id-badge">待配</span>`);
            const hideBtn = hit.hidden
                ? `<button class="id-mini-btn" onclick="toggleIdentityHidden('${identityEsc(hit.uid)}', false)">显示</button>`
                : `<button class="id-mini-btn" onclick="toggleIdentityHidden('${identityEsc(hit.uid)}', true)">隐藏</button>`;
            return `
                <div class="id-row${hit.hidden ? ' is-hidden' : ''}" data-idx="${gi}">
                    <div class="id-row-main">
                        <span class="id-uid">${identityEsc(hit.uid)}</span>
                        <span class="id-platform">${identityEsc(platform)}</span>
                        <span class="id-sources" title="${identityEsc(origin)}">${hit.source_count} 处</span>
                        ${badge}
                    </div>
                    <select class="form-select id-row-select" data-idx="${gi}"
                            data-uid="${identityEsc(hit.uid)}" data-platform="${identityEsc(platform)}"
                            onchange="onIdentityRowChange(this)">
                        ${identityArchiveOptions(selected)}
                    </select>
                    ${hideBtn}
                </div>`;
        }).join('');

        const emptyHint = visible.length
            ? ''
            : `<div class="archive-empty-inline" style="padding:10px 0;">该插件下的 UID 全部已隐藏（${hiddenInGroup} 个）。</div>`;

        return `
            <div class="id-group" data-idx="${gi}" data-plugin="${identityEsc(group.plugin)}">
                <div class="id-group-head">
                    <button class="id-group-toggle" onclick="toggleIdentityGroup(this)" title="展开/收起">▸</button>
                    <div class="id-group-name" title="${identityEsc(group.plugin)}">${identityEsc(group.label || group.plugin)}</div>
                    <div class="id-group-meta">
                        ${group.uid_count} 个 UID · 已配 ${group.configured}${hiddenInGroup ? ` · 隐藏 ${hiddenInGroup}` : ''}
                    </div>
                    <select class="form-select id-group-select" onchange="onIdentityGroupChange(this)">
                        <option value="">整组选择档案…</option>
                        ${identityArchiveOptions('').replace('<option value="">不配置</option>', '')}
                    </select>
                    <button class="id-mini-btn" title="只重扫这个插件，并把隐藏的 UID 找回来"
                            onclick="rescanIdentityPlugin('${identityEsc(group.plugin)}', this)">重扫</button>
                </div>
                <div class="id-group-body" style="display:none;">${rows}${emptyHint}</div>
            </div>`;
    }).join('');
}

function toggleIdentityGroup(btn) {
    const group = btn.closest('.id-group');
    if (!group) return;
    const body = group.querySelector('.id-group-body');
    if (!body) return;
    const open = body.style.display !== 'none';
    body.style.display = open ? 'none' : 'block';
    btn.innerText = open ? '▸' : '▾';
}

function onIdentityGroupChange(select) {
    const group = select.closest('.id-group');
    if (!group || !select.value) return;
    group.querySelectorAll('.id-row-select').forEach((rowSelect) => {
        rowSelect.value = select.value;
        onIdentityRowChange(rowSelect);
    });
    const body = group.querySelector('.id-group-body');
    const toggle = group.querySelector('.id-group-toggle');
    if (body) body.style.display = 'block';
    if (toggle) toggle.innerText = '▾';
}

function identityFindHit(platform, uid) {
    for (const group of identityMapState.groups) {
        for (const hit of group.uids || []) {
            if (hit.uid === uid && identityPlatform(hit) === platform) return hit;
        }
    }
    return null;
}

function onIdentityRowChange(select) {
    const uid = select.dataset.uid || '';
    const platform = select.dataset.platform || '';
    const key = `${platform}|${uid}`;
    if (!select.value) {
        identityMapState.dirty.delete(key);
        // 原本已配置的 UID 改回「不配置」= 解绑，不再只是从待保存列表里删掉
        const hit = identityFindHit(platform, uid);
        if (hit && hit.configured) {
            identityMapState.unbind.set(key, { platform, uid });
        } else {
            identityMapState.unbind.delete(key);
        }
    } else {
        identityMapState.unbind.delete(key);
        const archive = identityFindArchive(select.value);
        identityMapState.dirty.set(key, {
            platform,
            uid,
            archive_id: select.value,
            display_name: archive ? (archive.display_name || '') : '',
        });
    }
    updateIdentityDirtyHint();
}

function updateIdentityDirtyHint() {
    const el = document.getElementById('identity-scan-summary');
    if (!el) return;
    const n = identityMapState.dirty.size + identityMapState.unbind.size;
    if (!n) return;
    const base = el.dataset.base || el.innerText;
    el.dataset.base = base;
    el.innerText = `${base} · 待保存 ${n} 项`;
}

async function loadIdentityScan(plugin = '', resetHidden = false) {
    const host = document.getElementById('identity-groups');
    if (!plugin && host) host.innerHTML = '<div class="archive-empty">正在扫描插件身份…</div>';
    try {
        const params = [];
        if (plugin) params.push(`plugin=${encodeURIComponent(plugin)}`);
        if (resetHidden) params.push('reset_hidden=1');
        const scanUrl = '/api/identity/scan' + (params.length ? `?${params.join('&')}` : '');
        const [scanRes, archRes] = await Promise.all([
            fetch(scanUrl),
            fetch('/api/archives'),
        ]);
        const scanData = await scanRes.json();
        const archData = await archRes.json();
        const incoming = Array.isArray(scanData.groups) ? scanData.groups : [];
        identityMapState.archives = Array.isArray(archData.data) ? archData.data
            : (Array.isArray(archData.archives) ? archData.archives : []);

        if (plugin) {
            const idx = identityMapState.groups.findIndex(g => g.plugin === plugin);
            const next = incoming[0];
            if (next) {
                if (idx >= 0) identityMapState.groups[idx] = next;
                else identityMapState.groups.push(next);
            } else if (idx >= 0) {
                identityMapState.groups.splice(idx, 1);
            }
        } else {
            identityMapState.groups = incoming;
            identityMapState.dirty.clear();
        }
        identityMapState.loaded = true;
        renderIdentityGroups();
    } catch (err) {
        if (host) host.innerHTML = `<div class="archive-empty">扫描失败：${identityEsc(err.message || err)}</div>`;
    }
}

async function rescanIdentityPlugin(plugin, btn) {
    if (btn) { btn.disabled = true; btn.innerText = '扫描中…'; }
    try {
        await loadIdentityScan(plugin, true);
        if (typeof showToast === 'function') showToast(`已重扫 ${plugin}，隐藏的 UID 已恢复`);
    } finally {
        if (btn) { btn.disabled = false; btn.innerText = '重扫'; }
    }
}

async function toggleIdentityHidden(uid, hidden) {
    try {
        const res = await fetch('/api/identity/hide', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ uids: [uid], hidden }),
        });
        const data = await res.json();
        if (!data.ok) {
            if (typeof showToast === 'function') showToast('操作失败：' + (data.error || ''));
            return;
        }
        for (const group of identityMapState.groups) {
            for (const u of group.uids || []) {
                if (u.uid === uid) u.hidden = hidden;
            }
            group.hidden = (group.uids || []).filter(u => u.hidden).length;
        }
        renderIdentityGroups();
    } catch (err) {
        if (typeof showToast === 'function') showToast('操作失败：' + (err.message || err));
    }
}

function toggleIdentityShowHidden(checked) {
    identityMapState.showHidden = !!checked;
    renderIdentityGroups();
}

async function saveIdentityMap() {
    const items = Array.from(identityMapState.dirty.values());
    const unbinds = Array.from(identityMapState.unbind.values());
    if (!items.length && !unbinds.length) {
        if (typeof showToast === 'function') showToast('没有需要保存的改动');
        else alert('没有需要保存的改动');
        return;
    }
    try {
        let ok = 0;
        let failed = 0;
        if (items.length) {
            const res = await fetch('/api/identity/bind', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ items }),
            });
            const data = await res.json();
            ok += data.bound || 0;
            failed += data.failed || 0;
        }
        if (unbinds.length) {
            const res = await fetch('/api/identity/unbind', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ items: unbinds }),
            });
            const data = await res.json();
            ok += data.unbound || 0;
            failed += data.failed || 0;
        }
        const parts = [];
        if (items.length) parts.push(`绑定 ${items.length}`);
        if (unbinds.length) parts.push(`解绑 ${unbinds.length}`);
        const msg = failed
            ? `已保存 ${ok} 项，失败 ${failed} 项`
            : `已保存 ${ok} 项映射（${parts.join(' / ')}）`;
        if (typeof showToast === 'function') showToast(msg);
        else alert(msg);
        identityMapState.dirty.clear();
        identityMapState.unbind.clear();
        await loadIdentityScan();
    } catch (err) {
        if (typeof showToast === 'function') showToast('保存失败：' + (err.message || err));
        else alert('保存失败：' + (err.message || err));
    }
}

// 首次打开页面时若已停在身份映射，自动扫描；其余情况由 core.js 的 navigateTab 触发
document.addEventListener('DOMContentLoaded', () => {
    const tab = document.getElementById('tab-identity');
    if (tab && tab.classList.contains('active')) loadIdentityScan();
});