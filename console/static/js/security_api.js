// -*- coding: utf-8 -*-
// Stellaris - API 安全（本机访问限制 + 请求签名）

async function loadApiSecurity() {
    try {
        const resp = await fetch('/api/access_control', { cache: 'no-store' });
        if (!resp.ok) return;
        const data = await resp.json();
        const localOnly = document.getElementById('sec-local-only');
        const cidrs = document.getElementById('sec-allow-cidrs');
        const sigEnabled = document.getElementById('sec-signature-enabled');
        const state = document.getElementById('sec-signature-state');
        if (localOnly) localOnly.checked = Boolean(data.local_only);
        if (cidrs) cidrs.value = (data.allow_cidrs || []).join(', ');
        const signature = data.signature || {};
        if (sigEnabled) sigEnabled.checked = Boolean(signature.enabled);
        if (state) {
            const bits = [];
            bits.push(signature.secret_configured ? '密钥已配置' : '密钥未配置');
            if (signature.window_seconds) bits.push(`窗口 ${signature.window_seconds}s`);
            state.textContent = bits.join(' · ');
        }
    } catch (e) {
        console.warn('load api security failed:', e);
    }
}

function _parseCidrs(raw) {
    return String(raw || '')
        .split(/[,，\s]+/)
        .map(function (item) { return item.trim(); })
        .filter(Boolean);
}

async function saveApiSecurity() {
    try {
        const localOnly = Boolean(document.getElementById('sec-local-only')?.checked);
        const cidrs = _parseCidrs(document.getElementById('sec-allow-cidrs')?.value);
        if (localOnly && cidrs.length === 0) {
            // 空白名单 = 谁都进不来，先问一句
            if (!window.confirm('放行网段为空，开启后连控制台都打不开，确定继续？')) return;
        }
        const payload = { local_only: localOnly, allow_cidrs: cidrs };
        const signature = {
            enabled: Boolean(document.getElementById('sec-signature-enabled')?.checked),
        };
        const secretInput = document.getElementById('sec-secret');
        const secret = String(secretInput?.value || '').trim();
        if (secret) signature.secret = secret;
        payload.signature = signature;

        const resp = await fetch('/api/access_control', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await resp.json();
        if (!resp.ok || data.ok === false) throw new Error(data.error || '保存失败');
        if (secretInput) secretInput.value = '';
        if (typeof showToast === 'function') showToast('API 安全设置已保存');
        await loadApiSecurity();
    } catch (e) {
        if (typeof showToast === 'function') showToast('保存失败：' + (e.message || e));
    }
}

function generateApiSecret() {
    const input = document.getElementById('sec-secret');
    if (!input) return;
    const bytes = new Uint8Array(32);
    if (window.crypto && window.crypto.getRandomValues) {
        window.crypto.getRandomValues(bytes);
    } else {
        for (let i = 0; i < bytes.length; i += 1) bytes[i] = Math.floor(Math.random() * 256);
    }
    input.value = Array.from(bytes).map(function (b) {
        return b.toString(16).padStart(2, '0');
    }).join('');
    if (typeof showToast === 'function') showToast('已生成新密钥，记得点保存');
}

window.loadApiSecurity = loadApiSecurity;
window.saveApiSecurity = saveApiSecurity;
window.generateApiSecret = generateApiSecret;
