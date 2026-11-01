// -*- coding: utf-8 -*-
// Stellaris - Memory Tuning Panel (budgets, recall window, aggregator, live preview)

const MT_GROUPS = [
    {
        id: 'timeline',
        title: '注入与常驻',
        desc: '决定每轮对话真正进入提示词的时间轴大小，以及旧的记忆何时搬进缓冲区。',
    },
    {
        id: 'recall',
        title: '召回与候选池',
        desc: '模型主动回忆时能翻多深的旧账，以及候选池的扫描边界。',
    },
    {
        id: 'aggregation',
        title: '聚合与节奏',
        desc: 'L1 调用记录怎样结算成一条 L2 摘要；改完在下一轮扫描生效。',
    },
    {
        id: 'features',
        title: '功能开关',
        desc: '两处与记忆检索有关的运行时开关，保存后立即生效。',
    },
];

const MT_PRESETS = [
    {
        id: 'lean',
        name: '省 Token',
        desc: '1.5k 注入 · 3 天半衰',
        values: {
            timeline_budget_chars: 1500,
            timeline_keep_chars: 2500,
            recall_max_chars: 1000,
            candidate_chars: 150000,
            candidate_limit: 2000,
            lookback_days: 0,
            half_life_hours: 72,
            aggregator_idle_seconds: 600,
            aggregator_max_duration_seconds: 7200,
            aggregator_interval_seconds: 30,
        },
    },
    {
        id: 'balanced',
        name: '均衡',
        desc: '3k 注入 · 7 天半衰',
        values: {
            timeline_budget_chars: 3000,
            timeline_keep_chars: 4000,
            recall_max_chars: 2000,
            candidate_chars: 300000,
            candidate_limit: 3000,
            lookback_days: 0,
            half_life_hours: 168,
            aggregator_idle_seconds: 600,
            aggregator_max_duration_seconds: 7200,
            aggregator_interval_seconds: 30,
        },
    },
    {
        id: 'deep',
        name: '记得久',
        desc: '8k 注入 · 14 天半衰',
        values: {
            timeline_budget_chars: 8000,
            timeline_keep_chars: 12000,
            recall_max_chars: 4000,
            candidate_chars: 500000,
            candidate_limit: 5000,
            lookback_days: 0,
            half_life_hours: 336,
            aggregator_idle_seconds: 900,
            aggregator_max_duration_seconds: 10800,
            aggregator_interval_seconds: 30,
        },
    },
];

const MT_STATE = {
    payload: null,
    baseline: {},
    values: {},
    locked: new Set(),
    params: [],
    dirty: false,
    loading: false,
    previewTimer: null,
    previewSeq: 0,
};

function mtFormatNumber(value) {
    const number = Number(value);
    return Number.isFinite(number) ? number.toLocaleString('zh-CN') : '—';
}

function mtFormatHours(hours) {
    const value = Number(hours) || 0;
    if (value >= 24) return (value / 24).toFixed(1) + ' 天';
    if (value >= 1) return value.toFixed(1) + ' 小时';
    return Math.round(value * 60) + ' 分钟';
}

function mtFormatBytes(bytes) {
    const value = Number(bytes) || 0;
    if (value >= 1024 * 1024) return (value / 1024 / 1024).toFixed(1) + ' MB';
    if (value >= 1024) return (value / 1024).toFixed(0) + ' KB';
    return value + ' B';
}

function mtSourceText(source) {
    return { default: '默认', saved: '已保存', env: '环境变量' }[source] || '默认';
}

function mtParamMeta(key) {
    return MT_STATE.params.find((item) => item.key === key) || null;
}

function mtSameValue(key, left, right) {
    const param = mtParamMeta(key);
    if (param && param.kind === 'bool') return Boolean(left) === Boolean(right);
    return Number(left) === Number(right);
}

function mtUiBounds(param) {
    return {
        min: param.ui_min !== undefined ? param.ui_min : param.min,
        max: param.ui_max !== undefined ? param.ui_max : param.max,
    };
}

function mtClamp(param, value) {
    let number = Number(value);
    if (!Number.isFinite(number)) number = param.default;
    if (param.kind === 'int') number = Math.round(number);
    return Math.max(param.min, Math.min(param.max, number));
}

function mtFillPercent(param, value) {
    const bounds = mtUiBounds(param);
    const span = Number(bounds.max) - Number(bounds.min) || 1;
    const pct = ((Number(value) - Number(bounds.min)) / span) * 100;
    return Math.max(0, Math.min(100, pct));
}

function mtParamBadge(param, key) {
    const locked = MT_STATE.locked.has(key);
    if (locked) return '<span class="mt-source-badge" data-source="env">环境变量</span>';
    if (!mtSameValue(key, MT_STATE.values[key], MT_STATE.baseline[key])) {
        return '<span class="mt-source-badge" data-source="dirty">待保存</span>';
    }
    const source = (MT_STATE.payload.sources || {})[key] || 'default';
    return '<span class="mt-source-badge" data-source="' + source + '">' + mtSourceText(source) + '</span>';
}

function mtParamHeading(param) {
    return '<div class="mt-param-top">'
        + '<div class="mt-param-title">'
        + '<span class="mt-param-name">' + escapeHtml(param.label) + '</span>'
        + mtParamBadge(param, param.key)
        + '</div>'
        + '</div>'
        + '<div class="mt-param-hint">' + escapeHtml(param.hint) + '</div>';
}

function mtParamHtml(param) {
    const key = param.key;
    const value = MT_STATE.values[key];
    const locked = MT_STATE.locked.has(key);
    const lockClass = locked ? ' is-locked' : '';
    if (param.kind === 'bool') {
        return '<div class="mt-param mt-param-switch' + lockClass + '" data-key="' + key + '">'
            + '<div class="mt-param-copy">' + mtParamHeading(param) + '</div>'
            + '<div class="mt-param-control">'
            + '<label class="mt-switch">'
            + '<input type="checkbox" data-mt-bool="' + key + '" aria-label="' + escapeHtml(param.label) + '"'
            + (value ? ' checked' : '') + (locked ? ' disabled' : '') + '>'
            + '<span class="mt-switch-track"></span>'
            + '</label>'
            + '<div class="mt-switch-state" data-mt-switch-state="' + key + '">'
            + (value ? '开启' : '关闭') + '</div>'
            + '</div>'
            + '</div>';
    }
    const pct = mtFillPercent(param, value);
    const bounds = mtUiBounds(param);
    return '<div class="mt-param' + lockClass + '" data-key="' + key + '">'
        + mtParamHeading(param)
        + '<div class="mt-param-control">'
        + '<input class="mt-range" type="range" data-mt-range="' + key + '"'
        + ' aria-label="' + escapeHtml(param.label) + '"'
        + ' min="' + bounds.min + '" max="' + bounds.max + '" step="' + param.step + '"'
        + ' value="' + value + '" style="--fill:' + pct + '%"'
        + (locked ? ' disabled' : '') + '>'
        + '<div class="mt-number-wrap">'
        + '<input class="mt-number" type="number" data-mt-number="' + key + '"'
        + ' aria-label="' + escapeHtml(param.label) + '"'
        + ' min="' + param.min + '" max="' + param.max + '" step="' + param.step + '"'
        + ' value="' + value + '"' + (locked ? ' disabled' : '') + '>'
        + '<span class="mt-number-unit">' + escapeHtml(param.unit || '') + '</span>'
        + '</div>'
        + '</div>'
        + '</div>';
}

function mtRenderGroups() {
    const container = document.getElementById('mt-groups');
    if (!container) return;
    const byGroup = {};
    MT_STATE.params.forEach((param) => {
        (byGroup[param.group] = byGroup[param.group] || []).push(param);
    });
    container.innerHTML = MT_GROUPS
        .filter((group) => (byGroup[group.id] || []).length)
        .map((group, index) => {
            const list = byGroup[group.id] || [];
            return '<section class="mt-card mt-group">'
                + '<div class="mt-group-head">'
                + '<span class="mt-group-index">' + String(index + 1).padStart(2, '0') + '</span>'
                + '<div><div class="mt-group-title">' + escapeHtml(group.title) + '</div>'
                + '<div class="mt-group-desc">' + escapeHtml(group.desc) + '</div></div>'
                + '</div>'
                + '<div class="mt-group-body">' + list.map(mtParamHtml).join('') + '</div>'
                + '</section>';
        })
        .join('');
    mtBindParamEvents();
}

function mtBindParamEvents() {
    document.querySelectorAll('[data-mt-range]').forEach((input) => {
        input.addEventListener('input', () => mtOnRange(input));
    });
    document.querySelectorAll('[data-mt-number]').forEach((input) => {
        input.addEventListener('input', () => mtOnNumberInput(input));
        input.addEventListener('change', () => mtOnNumberCommit(input));
    });
    document.querySelectorAll('[data-mt-bool]').forEach((input) => {
        input.addEventListener('change', () => mtOnBool(input));
    });
}

function mtSyncParamUi(key) {
    const param = mtParamMeta(key);
    if (!param) return;
    const value = MT_STATE.values[key];
    const range = document.querySelector('[data-mt-range="' + key + '"]');
    if (range) {
        if (document.activeElement !== range) range.value = value;
        range.style.setProperty('--fill', mtFillPercent(param, value) + '%');
    }
    const number = document.querySelector('[data-mt-number="' + key + '"]');
    if (number && document.activeElement !== number) number.value = value;
    const toggle = document.querySelector('[data-mt-bool="' + key + '"]');
    if (toggle) toggle.checked = Boolean(value);
    const state = document.querySelector('[data-mt-switch-state="' + key + '"]');
    if (state) state.textContent = value ? '开启' : '关闭';
    const row = document.querySelector('.mt-param[data-key="' + key + '"]');
    const badge = row && row.querySelector('.mt-source-badge');
    if (badge) {
        const wrapper = document.createElement('div');
        wrapper.innerHTML = mtParamBadge(param, key);
        badge.replaceWith(wrapper.firstElementChild);
    }
}

function mtOnRange(input) {
    const key = input.dataset.mtRange;
    const param = mtParamMeta(key);
    if (!param) return;
    const value = mtClamp(param, input.value);
    MT_STATE.values[key] = value;
    mtSyncParamUi(key);
    mtAfterValueChange(key);
}

function mtOnNumberInput(input) {
    const key = input.dataset.mtNumber;
    const param = mtParamMeta(key);
    if (!param || input.value.trim() === '') return;
    const value = Number(input.value);
    if (!Number.isFinite(value)) return;
    MT_STATE.values[key] = value;
    const range = document.querySelector('[data-mt-range="' + key + '"]');
    if (range) range.style.setProperty('--fill', mtFillPercent(param, value) + '%');
    mtAfterValueChange(key);
}

function mtOnNumberCommit(input) {
    const key = input.dataset.mtNumber;
    const param = mtParamMeta(key);
    if (!param) return;
    const value = mtClamp(param, input.value);
    MT_STATE.values[key] = value;
    input.value = value;
    mtSyncParamUi(key);
    mtAfterValueChange(key);
}

function mtOnBool(input) {
    const key = input.dataset.mtBool;
    MT_STATE.values[key] = Boolean(input.checked);
    mtSyncParamUi(key);
    mtAfterValueChange(key);
}

function mtAfterValueChange(key) {
    mtUpdateDirty();
    if (key === 'timeline_budget_chars') mtSchedulePreview();
}

function mtValuesDiffer(key) {
    return !mtSameValue(key, MT_STATE.values[key], MT_STATE.baseline[key]);
}

function mtUpdateDirty() {
    MT_STATE.dirty = MT_STATE.params.some((param) => mtValuesDiffer(param.key));
    const badge = document.getElementById('mt-dirty-badge');
    if (badge) {
        badge.textContent = MT_STATE.dirty ? '有未保存修改' : '已同步';
        badge.classList.toggle('is-dirty', MT_STATE.dirty);
        badge.classList.toggle('is-synced', !MT_STATE.dirty);
    }
    const saveBtn = document.getElementById('mt-save-btn');
    if (saveBtn) saveBtn.disabled = !MT_STATE.dirty;
    MT_STATE.params.forEach((param) => mtSyncParamUi(param.key));
    mtRenderPresets();
}

function mtRenderPresets() {
    const container = document.getElementById('mt-presets');
    if (!container) return;
    container.innerHTML = MT_PRESETS.map((preset) => {
        const active = Object.keys(preset.values).every((key) => (
            !MT_STATE.locked.has(key) && mtSameValue(key, MT_STATE.values[key], preset.values[key])
        ));
        return '<button type="button" class="mt-preset' + (active ? ' active' : '') + '"'
            + ' onclick="applyMemoryPreset(\'' + preset.id + '\')">'
            + '<span>' + escapeHtml(preset.name) + '</span>'
            + '<span class="mt-preset-desc">' + escapeHtml(preset.desc) + '</span>'
            + '</button>';
    }).join('');
}

function applyMemoryPreset(presetId) {
    const preset = MT_PRESETS.find((item) => item.id === presetId);
    if (!preset || !MT_STATE.payload) return;
    Object.keys(preset.values).forEach((key) => {
        if (MT_STATE.locked.has(key)) return;
        const param = mtParamMeta(key);
        if (param) MT_STATE.values[key] = mtClamp(param, preset.values[key]);
    });
    MT_STATE.params.forEach((param) => mtSyncParamUi(param.key));
    MT_STATE.dirty = true;
    mtUpdateDirty();
    mtSchedulePreview(120);
}

function mtCollectValues() {
    const values = {};
    MT_STATE.params.forEach((param) => {
        if (MT_STATE.locked.has(param.key)) return;
        values[param.key] = param.kind === 'bool'
            ? Boolean(MT_STATE.values[param.key])
            : mtClamp(param, MT_STATE.values[param.key]);
    });
    return values;
}

async function loadMemoryTuning(force) {
    if (MT_STATE.loading) return;
    if (MT_STATE.payload && !force) return;
    MT_STATE.loading = true;
    try {
        const res = await fetch('/api/memory/settings');
        if (res.status === 401) {
            window.location.href = '/login';
            return;
        }
        const data = await res.json();
        if (!res.ok || !data.ok) throw new Error(data.error || '读取失败');
        mtApplyPayload(data);
    } catch (error) {
        const container = document.getElementById('mt-groups');
        if (container) {
            container.innerHTML = '<div class="mt-card"><div class="mt-preview-empty">'
                + '记忆参数读取失败：' + escapeHtml(error.message || error)
                + '<br><button type="button" class="mt-preset" style="margin-top:10px;"'
                + ' onclick="loadMemoryTuning(true)">重试</button></div></div>';
        }
    } finally {
        MT_STATE.loading = false;
    }
}

function mtApplyPayload(data) {
    MT_STATE.payload = data;
    MT_STATE.params = data.params || [];
    MT_STATE.baseline = Object.assign({}, data.settings || {});
    MT_STATE.values = Object.assign({}, data.settings || {});
    MT_STATE.locked = new Set(MT_STATE.params.filter((item) => item.locked).map((item) => item.key));
    MT_STATE.dirty = false;
    mtRenderGroups();
    mtRenderPresets();
    mtRenderStats(data.stats || {});
    mtLoadSnapshots();
    mtLoadBackupConfig();
    mtUpdateDirty();
    mtSchedulePreview(0);
}

function mtRenderStats(stats) {
    const container = document.getElementById('mt-stats-body');
    if (!container) return;
    if (!stats || !stats.ok) {
        container.innerHTML = '<div class="mt-preview-empty">记忆库统计暂不可用'
            + (stats && stats.error ? '：' + escapeHtml(stats.error) : '') + '</div>';
        return;
    }
    const rows = [
        ['活跃字数', mtFormatNumber(stats.active_chars) + ' 字'],
        ['覆盖跨度', mtFormatHours((Number(stats.coverage_days) || 0) * 24)],
        ['最近 24 小时', mtFormatNumber(stats.recent_24h) + ' 条'],
        ['缓冲区', mtFormatNumber(stats.buffer_events) + ' 条'],
        ['L3 原文', mtFormatNumber(stats.details) + ' 条 · ' + mtFormatBytes(stats.detail_bytes)],
        ['数据库', mtFormatBytes(stats.db_disk_bytes || stats.db_bytes)],
    ];
    container.innerHTML = '<div class="mt-store-big"><b>' + mtFormatNumber(stats.active_events)
        + '</b><span>条活跃记忆</span></div>'
        + '<div class="mt-store-rows">' + rows.map(([label, value]) => (
            '<div class="mt-store-row"><span>' + label + '</span><b>' + value + '</b></div>'
        )).join('') + '</div>';
    const usage = document.getElementById('mt-db-usage');
    if (usage) usage.textContent = mtFormatBytes(stats.db_disk_bytes || stats.db_bytes);
}

function mtSetPortabilityStatus(text, cls) {
    const el = document.getElementById('mt-portability-status');
    if (!el) return;
    el.textContent = text || '';
    el.className = 'mt-portability-status' + (cls ? ' ' + cls : '');
}

// ---------------- 快照与恢复（整库副本，不做自动清理） ----------------
function mtSetSnapshotStatus(text, cls) {
    const el = document.getElementById('mt-snapshot-status');
    if (!el) return;
    el.textContent = text || '';
    el.className = 'mt-portability-status' + (cls ? ' ' + cls : '');
}

function mtBindSnapshotActions() {
    const list = document.getElementById('mt-snapshot-list');
    if (!list || list.dataset.bound === '1') return;
    list.dataset.bound = '1';
    list.addEventListener('click', (event) => {
        const button = event.target.closest ? event.target.closest('[data-snapshot-action]') : null;
        if (!button || !list.contains(button)) return;
        const name = button.getAttribute('data-snapshot-name') || '';
        const action = button.getAttribute('data-snapshot-action');
        if (action === 'restore') mtRestoreSnapshot(name);
        if (action === 'delete') mtDeleteSnapshot(name);
    });
}

function mtRenderSnapshots(data) {
    const list = document.getElementById('mt-snapshot-list');
    const usage = document.getElementById('mt-snapshot-usage');
    const items = Array.isArray(data.items) ? data.items : [];
    if (usage) usage.textContent = `${data.count || 0} 份 · ${data.total_text || '0 B'}`;
    if (!list) return;
    if (!items.length) {
        list.innerHTML = '<div class="mt-portability-status">还没有快照；点「立即备份」会生成第一份。</div>';
        return;
    }
    list.innerHTML = items.map((item) => {
        const schema = item.schema_version ? ` · schema ${item.schema_version}` : '';
        return `
            <div class="mt-snapshot-row">
                <div class="mt-snapshot-main">
                    <span class="mt-snapshot-name">${escapeHtml(item.name)}</span>
                    <span class="mt-snapshot-meta">${escapeHtml(item.kind_label || '快照')} · ${escapeHtml(item.size_text || '')} · ${escapeHtml(item.created_text || '')}${schema}</span>
                </div>
                <div class="mt-snapshot-actions">
                    <button class="btn btn-secondary" type="button" data-snapshot-action="restore" data-snapshot-name="${escapeHtml(item.name)}">恢复</button>
                    <button class="btn btn-secondary" type="button" data-snapshot-action="delete" data-snapshot-name="${escapeHtml(item.name)}">删除</button>
                </div>
            </div>`;
    }).join('');
}

async function mtLoadSnapshots() {
    const list = document.getElementById('mt-snapshot-list');
    if (!list) return;
    mtBindSnapshotActions();
    try {
        const res = await fetch('/api/memory/snapshots', { cache: 'no-store' });
        if (res.status === 401) return;
        const data = await res.json();
        if (!res.ok || !data.ok) throw new Error(data.error || '读取失败');
        mtRenderSnapshots(data);
    } catch (error) {
        list.innerHTML = '<div class="mt-portability-status error">快照读取失败：'
            + escapeHtml(error.message || error) + '</div>';
    }
}

async function mtCreateSnapshot() {
    mtSetSnapshotStatus('正在备份当前记忆库…');
    try {
        const res = await fetch('/api/memory/snapshots/create', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({}),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) throw new Error(data.error || '备份失败');
        mtRenderSnapshots(data);
        const created = data.created && data.created.name ? data.created.name : '';
        mtSetSnapshotStatus(created ? `已备份：${created}` : '备份完成', 'ok');
        if (typeof showToast === 'function') showToast('记忆库已备份');
    } catch (error) {
        mtSetSnapshotStatus('备份失败：' + (error.message || error), 'error');
    }
}

async function mtRestoreSnapshot(name) {
    if (!name) return;
    if (!window.confirm(`恢复快照 ${name}？\n\n当前记忆会被覆盖；恢复前会自动再备份一份当前库。`)) return;
    mtSetSnapshotStatus('正在恢复…');
    try {
        const res = await fetch('/api/memory/snapshots/restore', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) throw new Error(data.error || '恢复失败');
        mtRenderSnapshots(data);
        const safety = data.safety && data.safety.name ? data.safety.name : '';
        mtSetSnapshotStatus(`已恢复 ${name}${safety ? '；恢复前快照：' + safety : ''}`, 'ok');
        if (typeof showToast === 'function') showToast('记忆库已恢复');
        if (typeof loadMemoryTuning === 'function') loadMemoryTuning(true);
    } catch (error) {
        mtSetSnapshotStatus('恢复失败：' + (error.message || error), 'error');
    }
}

async function mtDeleteSnapshot(name) {
    if (!name) return;
    if (!window.confirm(`删除快照 ${name}？\n\n只删这一份备份文件，不动当前记忆库。`)) return;
    mtSetSnapshotStatus('正在删除…');
    try {
        const res = await fetch('/api/memory/snapshots/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) throw new Error(data.error || '删除失败');
        mtRenderSnapshots(data);
        mtSetSnapshotStatus(`已删除 ${name}`, 'ok');
    } catch (error) {
        mtSetSnapshotStatus('删除失败：' + (error.message || error), 'error');
    }
}

// ---------------- 自动备份与巡检 ----------------
function mtFmtTime(ts) {
    const value = Number(ts) || 0;
    if (!value) return '—';
    return new Date(value * 1000).toLocaleString('zh-CN', { hour12: false });
}

function mtFillBackupForm(data) {
    const cfg = data.config || {};
    const enabled = document.getElementById('mt-backup-enabled');
    const interval = document.getElementById('mt-backup-interval');
    const check = document.getElementById('mt-backup-check-enabled');
    const dir = document.getElementById('mt-backup-dir');
    if (enabled) enabled.checked = !!cfg.enabled;
    if (interval) interval.value = cfg.interval_hours || 24;
    if (check) check.checked = cfg.check_enabled !== false;
    if (dir) dir.value = cfg.backup_dir || '';
}

function mtRenderBackupStatus(data) {
    const cfg = data.config || {};
    const state = document.getElementById('mt-backup-state');
    const status = document.getElementById('mt-backup-status');
    if (state) state.textContent = cfg.enabled ? `${cfg.interval_hours || 24} 小时/次` : '未启用';
    if (!status) return;
    const parts = [];
    if (cfg.last_backup_name) {
        parts.push(`上次备份 ${mtFmtTime(cfg.last_backup_at)}（${cfg.last_backup_name}）`);
    } else {
        parts.push('上次备份：还没有');
    }
    if (cfg.last_backup_error) parts.push(`备份失败：${cfg.last_backup_error}`);
    if (data.next_backup_at) parts.push(`下次 ${mtFmtTime(data.next_backup_at)}`);
    if (cfg.last_check_ok === true) {
        parts.push(`巡检通过 ${mtFmtTime(cfg.last_check_at)}`);
    } else if (cfg.last_check_ok === false) {
        parts.push(`巡检失败：${cfg.last_check_message || '未知错误'}（${mtFmtTime(cfg.last_check_at)}）`);
    } else {
        parts.push('巡检：未运行');
    }
    parts.push(`快照 ${data.snapshot_count || 0} 份 / ${data.snapshot_total_text || '0 B'}`);
    status.textContent = parts.join(' · ');
    status.className = 'mt-portability-status' + (cfg.last_check_ok === false ? ' error' : '');
}

async function mtLoadBackupConfig() {
    try {
        const res = await fetch('/api/memory/backup', { cache: 'no-store' });
        if (res.status === 401) return;
        const data = await res.json();
        if (!res.ok || !data.ok) throw new Error(data.error || '读取失败');
        mtFillBackupForm(data);
        mtRenderBackupStatus(data);
    } catch (error) {
        const status = document.getElementById('mt-backup-status');
        if (status) {
            status.textContent = '自动备份配置读取失败：' + (error.message || error);
            status.className = 'mt-portability-status error';
        }
    }
}

async function mtSaveBackupConfig() {
    const payload = {
        enabled: !!document.getElementById('mt-backup-enabled')?.checked,
        interval_hours: Number(document.getElementById('mt-backup-interval')?.value || 24),
        backup_dir: (document.getElementById('mt-backup-dir')?.value || '').trim(),
        check_enabled: !!document.getElementById('mt-backup-check-enabled')?.checked,
    };
    try {
        const res = await fetch('/api/memory/backup/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) throw new Error(data.error || '保存失败');
        mtFillBackupForm(data);
        mtRenderBackupStatus(data);
        if (typeof showToast === 'function') showToast('自动备份设置已保存');
    } catch (error) {
        const status = document.getElementById('mt-backup-status');
        if (status) {
            status.textContent = '保存失败：' + (error.message || error);
            status.className = 'mt-portability-status error';
        }
    }
}

async function mtRunCheck() {
    const status = document.getElementById('mt-backup-status');
    if (status) {
        status.textContent = '正在巡检…';
        status.className = 'mt-portability-status';
    }
    try {
        const res = await fetch('/api/memory/backup/check', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({}),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) throw new Error(data.error || '巡检失败');
        mtFillBackupForm(data);
        mtRenderBackupStatus(data);
        const result = data.check || {};
        if (typeof showToast === 'function') {
            showToast(result.ok ? '记忆库巡检通过' : '巡检发现问题：' + (result.message || ''));
        }
    } catch (error) {
        if (status) {
            status.textContent = '巡检失败：' + (error.message || error);
            status.className = 'mt-portability-status error';
        }
    }
}

async function mtExportMemory() {
    mtSetPortabilityStatus('正在打包…');
    try {
        const resp = await fetch('/api/memory/export');
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'stellaris_memory_' + new Date().toISOString().slice(0, 10) + '.json';
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 2000);
        mtSetPortabilityStatus('导出已开始，文件保存到浏览器下载目录。', 'ok');
    } catch (e) {
        mtSetPortabilityStatus('导出失败：' + (e.message || e), 'error');
    }
}

function mtPickImportFile() {
    document.getElementById('mt-import-file')?.click();
}

async function mtImportMemory(input) {
    const file = input?.files?.[0];
    if (!file) return;
    if (file.size > 50 * 1024 * 1024) {
        mtSetPortabilityStatus('文件超过 50MB，已取消。', 'error');
        input.value = '';
        return;
    }
    const mode = document.getElementById('mt-import-mode')?.value || 'merge';
    const format = document.getElementById('mt-import-format')?.value || 'auto';
    if (mode === 'replace' && !window.confirm('清空当前记忆并导入？会先自动备份，但当前记忆库会被替换。')) {
        input.value = '';
        return;
    }
    mtSetPortabilityStatus('正在读取并导入…');
    try {
        const content = await file.text();
        const resp = await fetch('/api/memory/import', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content, mode, format }),
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok || !data.ok) throw new Error(data.error || ('HTTP ' + resp.status));
        const counts = Object.entries(data.counts || {}).map(([k, v]) => k + ' ' + v).join(' · ');
        mtSetPortabilityStatus('导入完成：' + (counts || '0 条') + '；备份：' + (data.backup || '已生成'), 'ok');
        await loadMemoryTuning(true);
    } catch (e) {
        mtSetPortabilityStatus('导入失败：' + (e.message || e), 'error');
    } finally {
        input.value = '';
    }
}

function mtSetPreviewState(text, cls) {
    const pill = document.getElementById('mt-preview-state');
    if (!pill) return;
    pill.textContent = text;
    pill.className = 'mt-live-pill' + (cls ? ' ' + cls : '');
}

function mtSchedulePreview(delay) {
    clearTimeout(MT_STATE.previewTimer);
    MT_STATE.previewTimer = setTimeout(() => mtRefreshPreview(), Number(delay) || 0);
}

async function mtRefreshPreview() {
    if (!MT_STATE.payload) return;
    const param = mtParamMeta('timeline_budget_chars');
    const budget = param ? mtClamp(param, MT_STATE.values.timeline_budget_chars) : 3000;
    const seq = ++MT_STATE.previewSeq;
    mtSetPreviewState('计算中', 'is-busy');
    try {
        const res = await fetch('/api/memory/timeline_curve?budget=' + Math.round(budget));
        const data = await res.json();
        if (seq !== MT_STATE.previewSeq) return;
        if (!res.ok || !data.ok) throw new Error(data.error || '预览失败');
        mtRenderPreview(data, budget);
        mtSetPreviewState('已更新', 'is-ok');
    } catch (error) {
        if (seq !== MT_STATE.previewSeq) return;
        mtSetPreviewState('失败', 'is-error');
        const body = document.getElementById('mt-preview-body');
        if (body) body.innerHTML = '<div class="mt-preview-empty">预览失败：'
            + escapeHtml(error.message || error) + '</div>';
    }
}

function mtRenderPreview(data, budget) {
    const body = document.getElementById('mt-preview-body');
    if (!body) return;
    const summary = data.summary || {};
    const kept = Number(summary.kept_chars) || 0;
    const original = Number(summary.original_chars) || 0;
    const ratio = original ? Math.round((kept / original) * 100) : 0;
    const tierChars = summary.per_tier_kept_chars || [0, 0, 0];
    const tierTotal = tierChars.reduce((sum, value) => sum + Number(value || 0), 0) || 1;
    const segments = tierChars.map((value, index) => {
        const width = (Number(value || 0) / tierTotal) * 100;
        return '<span class="mt-tier-seg t' + index + '" style="width:' + width + '%"></span>';
    }).join('');
    const samples = (data.samples || []).slice(0, 4).map((item) => {
        const tier = Number(item.tier) || 0;
        const labels = ['完整', '轻压', '深压'];
        return '<div class="mt-sample">'
            + '<div class="mt-sample-head">'
            + '<span class="mt-sample-time">' + escapeHtml(item.time || '') + '</span>'
            + '<span class="mt-sample-tier t' + tier + '">' + (labels[tier] || '压缩') + '</span>'
            + '</div>'
            + '<div class="mt-sample-text">' + escapeHtml(item.text || '') + '</div>'
            + '</div>';
    }).join('');
    const empty = Number(summary.events) === 0;
    body.innerHTML = '<div class="mt-preview-hero">'
        + '<div class="mt-preview-number"><b>' + mtFormatNumber(kept) + '</b><span>字 / 轮</span></div>'
        + '<div class="mt-preview-budget"><b>' + mtFormatNumber(budget) + ' 字预算</b>'
        + (original ? '压缩到 ' + ratio + '%' : '暂无可预览事件') + '</div>'
        + '</div>'
        + '<div class="mt-tier-bar">' + (empty ? '' : segments) + '</div>'
        + '<div class="mt-tier-legend">'
        + '<span><i class="t0"></i>完整 ' + mtFormatNumber((summary.per_tier || [])[0] || 0) + '</span>'
        + '<span><i class="t1"></i>轻压 ' + mtFormatNumber((summary.per_tier || [])[1] || 0) + '</span>'
        + '<span><i class="t2"></i>深压 ' + mtFormatNumber((summary.per_tier || [])[2] || 0) + '</span>'
        + '</div>'
        + '<div class="mt-preview-grid">'
        + '<div class="mt-mini-stat"><span>覆盖事件</span><b>' + mtFormatNumber(summary.events || 0) + ' 条</b></div>'
        + '<div class="mt-mini-stat"><span>时间跨度</span><b>' + mtFormatHours(data.coverage_hours) + '</b></div>'
        + '<div class="mt-mini-stat"><span>原始字数</span><b>' + mtFormatNumber(original) + '</b></div>'
        + '<div class="mt-mini-stat"><span>压缩比</span><b>' + ratio + '%</b></div>'
        + '</div>'
        + (empty
            ? '<div class="mt-preview-empty">时间轴还没有可注入的事件。先去聊两句，或把预算调大试试。</div>'
            : '<div class="mt-samples"><div class="mt-samples-title"><span>最近注入样本</span>'
                + '<span>前 4 条</span></div>' + samples + '</div>');
}

async function saveMemoryTuning() {
    if (!MT_STATE.payload || !MT_STATE.dirty) return;
    const button = document.getElementById('mt-save-btn');
    const oldText = button ? button.textContent : '';
    if (button) {
        button.disabled = true;
        button.textContent = '保存中…';
    }
    try {
        const res = await fetch('/api/memory/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ settings: mtCollectValues() }),
        });
        const data = await res.json();
        if (!res.ok || !data.ok) throw new Error(data.error || '保存失败');
        mtApplyPayload(data);
        if (typeof showToast === 'function') showToast('记忆参数已保存，下一轮对话生效');
    } catch (error) {
        if (typeof showToast === 'function') showToast('保存失败：' + (error.message || error));
    } finally {
        if (button) button.textContent = oldText;
        mtUpdateDirty();
    }
}

async function resetMemoryTuning() {
    if (!MT_STATE.payload) return;
    if (!window.confirm('恢复推荐值会清除已经保存的调参覆盖，确定继续？')) return;
    try {
        const res = await fetch('/api/memory/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ reset: true }),
        });
        const data = await res.json();
        if (!res.ok || !data.ok) throw new Error(data.error || '恢复失败');
        mtApplyPayload(data);
        if (typeof showToast === 'function') showToast('已恢复推荐值');
    } catch (error) {
        if (typeof showToast === 'function') showToast('恢复失败：' + (error.message || error));
    }
}
