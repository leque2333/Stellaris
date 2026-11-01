// -*- coding: utf-8 -*-
// Stellaris - Access Policy (guest mode)

async function loadAccessPolicy() {
    try {
        const resp = await fetch('/api/access_policy', { cache: 'no-store' });
        if (!resp.ok) return;
        const data = await resp.json();
        const policy = data.policy || {};
        const guest = document.getElementById('policy-guest-enabled');
        const timeline = document.getElementById('policy-guest-timeline');
        const affection = document.getElementById('policy-guest-affection');
        if (guest) guest.checked = Boolean(policy.guest_enabled);
        if (timeline) timeline.checked = Boolean(policy.guest_timeline_enabled);
        if (affection) affection.value = String(policy.guest_default_affection ?? 50);
    } catch (e) {
        console.warn('load access policy failed:', e);
    }
}

async function saveAccessPolicy() {
    try {
        const payload = {
            guest_enabled: Boolean(document.getElementById('policy-guest-enabled')?.checked),
            guest_timeline_enabled: Boolean(document.getElementById('policy-guest-timeline')?.checked),
            guest_default_affection: parseInt(document.getElementById('policy-guest-affection')?.value || '50', 10)
        };
        const resp = await fetch('/api/access_policy', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await resp.json();
        if (!resp.ok || data.ok === false) throw new Error(data.error || '保存失败');
        if (typeof showToast === 'function') showToast('访问策略已保存');
        await loadAccessPolicy();
    } catch (e) {
        if (typeof showToast === 'function') showToast('保存失败：' + (e.message || e));
    }
}

window.loadAccessPolicy = loadAccessPolicy;
window.saveAccessPolicy = saveAccessPolicy;

async function clearLedger() {
    if (!window.confirm('确定清空全部 Token 流水和费用记录？此操作不可恢复。')) return;
    try {
        const resp = await fetch('/api/ledger/clear', { method: 'POST' });
        const data = await resp.json();
        if (!resp.ok || data.ok === false) throw new Error(data.error || '清空失败');

        // 同步归零前端缓存，避免弹窗/配额还显示旧金额
        if (typeof globalModelsConfig !== 'undefined' && globalModelsConfig) {
            globalModelsConfig.credits = Object.assign({}, globalModelsConfig.credits || {}, {
                total_quota: 0,
                balance: 0,
                used_quota: 0,
            });
            ['master_credentials', 'function_credentials'].forEach(function (pool) {
                (globalModelsConfig[pool] || []).forEach(function (cred) {
                    cred.balance = 0;
                    if ('allocated_quota' in cred) cred.allocated_quota = 0;
                });
            });
        }
        const symbol = (window.lastDashboardData?.tokens?.credits?.balances?.[0]?.symbol) || '$';
        const quotaEl = document.getElementById('hero-granted-quota');
        if (quotaEl) quotaEl.innerText = `${symbol}0.00`;

        if (typeof showToast === 'function') showToast(`已清空 ${data.cleared || 0} 条流水，金额已归零`);
        if (typeof fetchTokenStats === 'function') await fetchTokenStats(false);
        if (typeof loadDetailedLedger === 'function') loadDetailedLedger();
        if (typeof updateCurrencyAndBalances === 'function') updateCurrencyAndBalances();
    } catch (e) {
        if (typeof showToast === 'function') showToast('清空失败：' + (e.message || e));
    }
}
window.clearLedger = clearLedger;
