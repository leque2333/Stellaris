// -*- coding: utf-8 -*-
// Stellaris - Person archives (profile, identities, affection, facts)

const archivesState = { list: [], current: null };

function archiveEsc(value) {
    return typeof escapeHtml === 'function' ? escapeHtml(value) : String(value == null ? '' : value);
}

function archiveRoleLabel(role) {
    return role === 'admin' ? '管理员' : '访客';
}

function archiveInitial(name) {
    const text = String(name || '?').trim();
    return text ? text.charAt(0).toUpperCase() : '?';
}

function renderArchiveList() {
    const listEl = document.getElementById('archive-list');
    const countEl = document.getElementById('archive-count');
    if (!listEl) return;

    const sorted = [...archivesState.list].sort((a, b) => {
        if (a.role === b.role) return String(a.display_name || '').localeCompare(String(b.display_name || ''));
        return a.role === 'admin' ? -1 : 1;
    });

    if (countEl) countEl.innerText = String(sorted.length);
    if (!sorted.length) {
        listEl.innerHTML = '<div class="archive-empty">还没有人物档案</div>';
        return;
    }

    listEl.innerHTML = sorted.map((a) => {
        const active = archivesState.current === a.archive_id ? ' active' : '';
        const identityCount = Array.isArray(a.identities) ? a.identities.length : 0;
        return `
            <div class="archive-card${active}" data-archive-id="${a.archive_id}" onclick="selectArchive('${a.archive_id}', this)">
                <div class="archive-avatar">${archiveEsc(archiveInitial(a.display_name || a.archive_id))}</div>
                <div style="min-width:0;flex:1;">
                    <div class="archive-name">${archiveEsc(a.display_name || a.archive_id)}</div>
                    <div class="archive-meta">${archiveEsc(archiveRoleLabel(a.role))} · ${identityCount} 个平台身份</div>
                </div>
                <span class="archive-role ${a.role === 'admin' ? 'admin' : ''}">${archiveEsc(archiveRoleLabel(a.role))}</span>
            </div>
        `;
    }).join('');
}

function renderArchiveIdentities(identities) {
    const el = document.getElementById('archive-identities');
    if (!el) return;
    const list = Array.isArray(identities) ? identities : [];
    if (!list.length) {
        el.innerHTML = '<span style="font-size:12px;color:var(--text-muted);">未绑定平台账号</span>';
        return;
    }
    el.innerHTML = list.map((it) => {
        const platform = archiveEsc(it.platform || '未知平台');
        const uid = archiveEsc(it.platform_user_id || '');
        const name = it.display_name ? ' · ' + archiveEsc(it.display_name) : '';
        return `<span style="font-size:12px;padding:4px 10px;border-radius:999px;background:rgba(0,0,0,0.04);color:var(--text-main);">${platform} · ${uid}${name}</span>`;
    }).join('');
}

function renderArchiveAffection(value, minValue, maxValue) {
    const el = document.getElementById('archive-affection');
    if (!el) return;
    const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, Number(v) || 0));
    const unknown = (value === null || value === undefined || value === '');
    const score = unknown ? null : clamp(value, 0, 100);
    const lo = (minValue === null || minValue === undefined) ? 0 : clamp(minValue, 0, 100);
    const hi = (maxValue === null || maxValue === undefined) ? 100 : clamp(maxValue, 0, 100);

    // 整段窗口紫色（可用区），当前值用圆点标在条上；未定时不画圆点，右侧显示 ?
    const dot = unknown
        ? ''
        : `<div style="position:absolute;top:1px;left:${score}%;width:12px;height:12px;margin-left:-6px;border-radius:50%;background:#fff;border:2px solid #4f46e5;box-sizing:border-box;"></div>`;
    const badge = unknown
        ? `<span style="font-size:13px;font-weight:600;color:#94a3b8;min-width:20px;text-align:right;" title="还没有交流过，好感度未定">?</span>`
        : '';

    el.innerHTML = `
        <div style="display:flex;align-items:center;gap:8px;">
            <div style="position:relative;flex:1;height:14px;">
                <div style="position:absolute;top:2px;left:0;right:0;height:10px;border-radius:999px;background:#e5e7eb;overflow:hidden;">
                    <div style="position:absolute;left:${lo}%;width:${Math.max(0, hi - lo)}%;height:100%;background:#6366f1;"></div>
                </div>
                ${dot}
            </div>
            ${badge}
        </div>`;
}

function previewAffectionRange() {
    const detail = archivesState.detail;
    if (!detail) return;
    const minInput = document.getElementById('affection-min');
    const maxInput = document.getElementById('affection-max');
    const lo = minInput && minInput.value !== '' ? Number(minInput.value) : 0;
    const hi = maxInput && maxInput.value !== '' ? Number(maxInput.value) : 100;
    renderArchiveAffection(detail.affection, lo, hi);
}

function renderArchiveSettings(settings) {
    const el = document.getElementById('archive-settings-text');
    if (!el) return;
    const list = Array.isArray(settings) ? settings : [];
    el.value = list.map((s) => {
        const label = String(s.label || '').trim();
        const content = String(s.content || '').trim();
        return (label && label !== '设定') ? `${label}：${content}` : content;
    }).join('\n');
}

function renderArchiveFacts(facts) {
    const el = document.getElementById('archive-facts');
    if (!el) return;
    const list = Array.isArray(facts) ? facts : [];
    if (!list.length) {
        el.innerHTML = '<div class="archive-empty-inline">还没有沉淀出长效事实。</div>';
        return;
    }
    el.innerHTML = list.map((f) => {
        const text = archiveEsc(typeof f === 'string' ? f : (f.content || ''));
        const category = (typeof f === 'object' && f.category)
            ? `<span style="font-size:11px;color:var(--text-muted);margin-left:8px;">${archiveEsc(f.category)}</span>`
            : '';
        return `<div style="font-size:13px;line-height:1.7;padding:6px 0;border-bottom:1px solid rgba(0,0,0,0.04);">${text}${category}</div>`;
    }).join('');
}

function renderArchiveDetail(archive) {
    const detailEl = document.getElementById('archive-detail');
    const emptyEl = document.getElementById('archive-empty');
    if (!detailEl) return;
    if (!archive) {
        detailEl.style.display = 'none';
        if (emptyEl) emptyEl.style.display = 'block';
        return;
    }

    detailEl.style.display = 'block';
    if (emptyEl) emptyEl.style.display = 'none';
    archivesState.detail = archive;

    const titleEl = document.getElementById('archive-detail-name');
    const idEl = document.getElementById('archive-detail-id');
    const roleEl = document.getElementById('archive-detail-role');
    const nameInput = document.getElementById('archive-detail-name-input');
    const statusSelect = document.getElementById('archive-detail-status');

    if (titleEl) titleEl.innerText = archive.display_name || archive.archive_id;
    if (idEl) idEl.innerText = archive.archive_id;
    if (roleEl) {
        roleEl.innerText = archiveRoleLabel(archive.role);
        roleEl.classList.toggle('admin', archive.role === 'admin');
    }
    if (nameInput) nameInput.value = archive.display_name || '';
    if (statusSelect) statusSelect.value = archive.status || 'active';

    const minInput = document.getElementById('affection-min');
    const maxInput = document.getElementById('affection-max');
    if (minInput) minInput.value = (archive.affection_min === null || archive.affection_min === undefined) ? 0 : archive.affection_min;
    if (maxInput) maxInput.value = (archive.affection_max === null || archive.affection_max === undefined) ? 100 : archive.affection_max;

    renderArchiveIdentities(archive.identities || []);
    renderArchiveAffection(archive.affection, archive.affection_min, archive.affection_max);
    renderArchiveSettings(archive.settings || []);
    renderArchiveFacts(archive.facts || []);
}

async function loadArchives() {
    try {
        const resp = await fetch('/api/archives');
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        const payload = await resp.json();
        archivesState.list = Array.isArray(payload.data) ? payload.data : [];
        renderArchiveList();

        const current = archivesState.current;
        if (current && archivesState.list.some((a) => a.archive_id === current)) {
            await selectArchive(current);
        } else if (archivesState.list.length) {
            await selectArchive(archivesState.list[0].archive_id);
        } else {
            renderArchiveDetail(null);
        }
    } catch (e) {
        console.warn('loadArchives failed:', e);
        if (typeof showToast === 'function') showToast('加载档案失败');
    }
}

async function selectArchive(archiveId) {
    archivesState.current = archiveId;
    document.querySelectorAll('.archive-card').forEach((el) => {
        el.classList.toggle('active', el.dataset.archiveId === archiveId);
    });

    try {
        const resp = await fetch('/api/archives/' + encodeURIComponent(archiveId));
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        const archive = await resp.json();
        renderArchiveDetail(archive);
    } catch (e) {
        console.warn('selectArchive failed:', e);
        if (typeof showToast === 'function') showToast('加载档案详情失败');
    }
}

function openCreateArchive() {
    const modal = document.getElementById('archive-create-modal');
    if (!modal) return;
    const name = document.getElementById('archive-create-name');
    if (name) name.value = '';
    modal.classList.add('show');
}

function closeCreateArchive() {
    document.getElementById('archive-create-modal')?.classList.remove('show');
}

async function submitCreateArchive() {
    const displayName = (document.getElementById('archive-create-name')?.value || '').trim();
    if (!displayName) {
        if (typeof showToast === 'function') showToast('请填写名称');
        return;
    }

    try {
        const role = document.getElementById('archive-create-role')?.value || 'guest';
        const resp = await fetch('/api/archives', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ display_name: displayName, role })
        });
        const payload = await resp.json().catch(() => ({}));
        if (!resp.ok) {
            if (typeof showToast === 'function') showToast(payload?.error?.message || '创建失败');
            return;
        }
        closeCreateArchive();
        if (typeof showToast === 'function') showToast('人物档案已创建');
        archivesState.current = payload.archive_id;
        await loadArchives();
        await selectArchive(payload.archive_id);
    } catch (e) {
        console.warn('create archive failed:', e);
        if (typeof showToast === 'function') showToast('创建失败');
    }
}

async function saveArchive() {
    const archiveId = archivesState.current;
    if (!archiveId) return;
    const displayName = document.getElementById('archive-detail-name-input')?.value || '';
    const status = document.getElementById('archive-detail-status')?.value || 'active';

    try {
        const resp = await fetch('/api/archives/' + encodeURIComponent(archiveId), {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ display_name: displayName, status })
        });
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        if (typeof showToast === 'function') showToast('档案已保存');
        await loadArchives();
    } catch (e) {
        console.warn('save archive failed:', e);
        if (typeof showToast === 'function') showToast('保存失败');
    }
}

async function saveAffectionRange() {
    const archiveId = archivesState.current;
    if (!archiveId) return;
    const minInput = document.getElementById('affection-min');
    const maxInput = document.getElementById('affection-max');
    const minValue = parseInt(minInput?.value || '0', 10);
    const maxValue = parseInt(maxInput?.value || '100', 10);
    if (Number.isNaN(minValue) || Number.isNaN(maxValue)) {
        if (typeof showToast === 'function') showToast('请填写有效数字');
        return;
    }
    if (minValue < 0 || maxValue > 100 || minValue > maxValue) {
        if (typeof showToast === 'function') showToast('需满足 0 ≤ 下限 ≤ 上限 ≤ 100');
        return;
    }
    try {
        const resp = await fetch('/api/archives/' + encodeURIComponent(archiveId), {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ affection_min: minValue, affection_max: maxValue })
        });
        const payload = await resp.json().catch(() => ({}));
        if (!resp.ok) {
            if (typeof showToast === 'function') showToast(payload?.error?.message || '保存失败');
            return;
        }
        if (typeof showToast === 'function') showToast('好感度范围已保存');
        renderArchiveDetail(payload);
    } catch (e) {
        console.warn('save affection range failed:', e);
        if (typeof showToast === 'function') showToast('保存失败');
    }
}

async function saveArchiveSettings() {
    const archiveId = archivesState.current;
    if (!archiveId) return;
    const text = document.getElementById('archive-settings-text')?.value || '';
    try {
        const resp = await fetch('/api/archives/' + encodeURIComponent(archiveId) + '/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text })
        });
        const payload = await resp.json().catch(() => ({}));
        if (!resp.ok) {
            if (typeof showToast === 'function') showToast(payload?.error?.message || '保存失败');
            return;
        }
        if (typeof showToast === 'function') showToast(`设定已保存（${payload.count || 0} 条）`);
        await selectArchive(archiveId);
    } catch (e) {
        console.warn('save settings failed:', e);
        if (typeof showToast === 'function') showToast('保存失败');
    }
}

window.loadArchives = loadArchives;
window.selectArchive = selectArchive;
window.openCreateArchive = openCreateArchive;
window.closeCreateArchive = closeCreateArchive;
window.submitCreateArchive = submitCreateArchive;
window.saveArchive = saveArchive;
window.saveAffectionRange = saveAffectionRange;
window.saveArchiveSettings = saveArchiveSettings;

window.previewAffectionRange = previewAffectionRange;
