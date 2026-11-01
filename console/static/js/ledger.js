// -*- coding: utf-8 -*-
// Stellaris - Ledger & Billing Module
// 数据源：GET /api/ledger -> get_token_analytics()
// 口径：
//   1. “合计”只统计 Token 与调用次数，不再把不同币种金额相加；
//   2. 金额 / Token / 调用次数按主控凭证、执行凭证分开；
//   3. 每组金额再按凭证单位（USD / CNY）分别列示。

(function () {
    const RECHARGE_SOURCES = new Set(['人工充值', '账户充值', '系统注资']);

    function escapeHtml(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function formatNumber(value) {
        return Number(value || 0).toLocaleString();
    }

    function setText(id, value) {
        const el = document.getElementById(id);
        if (el) el.innerText = value;
    }

    function groupClass(kind) {
        if (kind === 'master') return 'is-master';
        if (kind === 'function') return 'is-function';
        return 'is-other';
    }

    function emptyGroup(kind, label) {
        return {
            credential_type: kind,
            label: label,
            costs: [],
            today_calls: 0,
            today_tokens: 0,
            overall_calls: 0,
            overall_tokens: 0,
        };
    }

    function metricCard(label, value, badge) {
        const badgeHtml = badge
            ? `<span class="ledger-metric-badge">${escapeHtml(badge)}</span>`
            : '';
        return `
            <div class="ledger-stat-box ledger-metric-card">
                <div class="ledger-metric-head">
                    <span class="ledger-metric-label">${escapeHtml(label)}</span>
                    ${badgeHtml}
                </div>
                <div class="ledger-metric-value">${escapeHtml(value)}</div>
            </div>`;
    }

    function accountingRow(group, kind) {
        const costs = Array.isArray(group.costs) ? group.costs : [];
        const cards = [];

        if (!costs.length) {
            cards.push(metricCard('消耗总金额', '—', ''));
            cards.push(metricCard('今日消耗金额', '—', ''));
        } else {
            // 所有币种的累计金额先列，再列所有币种的今日金额；每行固定 4 张自动换行
            costs.forEach(function (cost) {
                cards.push(metricCard('消耗总金额', cost.overall_cost_formatted || '—', cost.currency || ''));
            });
            costs.forEach(function (cost) {
                cards.push(metricCard('今日消耗金额', cost.today_cost_formatted || '—', cost.currency || ''));
            });
        }
        cards.push(metricCard('今日消耗 Tokens', formatNumber(group.today_tokens), ''));
        cards.push(metricCard('今日调用次数', `${formatNumber(group.today_calls)} 次`, ''));

        const subtitleClass = kind === 'function' ? 'is-function' : 'is-master';
        return `
            <section class="ledger-accounting-row">
                <div class="ledger-accounting-subtitle ${subtitleClass}">${escapeHtml(group.label || (kind === 'function' ? '执行凭证' : '主控凭证'))}</div>
                <div class="ledger-metric-grid">
                    ${cards.join('')}
                </div>
            </section>`;
    }

    function renderAccountingRows(groups) {
        const container = document.getElementById('ledger-accounting-rows');
        if (!container) return;

        const safeGroups = Array.isArray(groups) ? groups : [];
        const byKind = {};
        safeGroups.forEach(function (group) {
            byKind[group.credential_type] = group;
        });
        const master = byKind.master || emptyGroup('master', '主控凭证');
        const func = byKind.function || emptyGroup('function', '执行凭证');

        container.innerHTML = accountingRow(master, 'master') + accountingRow(func, 'function');
    }

    function renderSourceCell(record) {
        const channel = String(record.session_type || record.source || '').trim();
        const tools = String(record.tools || '').trim();
        if (!channel && !tools) return '<span class="ledger-muted-cell">--</span>';
        const parts = [];
        if (channel) parts.push(`<span class="ledger-source-channel">${escapeHtml(channel)}</span>`);
        if (tools) {
            const names = tools.split(',').filter(Boolean);
            names.forEach(function (n) {
                parts.push(`<span class="badge ledger-tool-badge">${escapeHtml(n)}</span>`);
            });
        }
        return parts.join(' ');
    }

    function renderCredentialCell(record) {
        const kind = String(record.credential_type || '');
        const isRecharge = Boolean(record.is_recharge || RECHARGE_SOURCES.has(record.source));
        let label = record.credential_label || '';
        if (!label) {
            if (isRecharge) label = '充值';
            else if (kind === 'master') label = '主控凭证';
            else if (kind === 'function') label = '执行凭证';
            else label = '未归类';
        }
        const currency = record.currency || 'USD';
        return `
            <span class="ledger-credential-badge ${isRecharge ? 'is-recharge' : groupClass(kind)}">${escapeHtml(label)}</span>
            <span class="ledger-credential-currency">${escapeHtml(currency)}</span>`;
    }

    function formatTime(record) {
        let timeStr = record.time || '--:--:--';
        if (record.timestamp) {
            const d = new Date(record.timestamp * 1000);
            const now = new Date();
            const isToday = d.toDateString() === now.toDateString();
            const hms = d.toTimeString().slice(0, 8);
            if (isToday) {
                timeStr = hms;
            } else {
                const mon = String(d.getMonth() + 1).padStart(2, '0');
                const day = String(d.getDate()).padStart(2, '0');
                timeStr = `${mon}-${day} ${hms}`;
            }
        }
        return timeStr;
    }

    function renderRecords(records) {
        window.__ledgerRecords = Array.isArray(records) ? records : [];
        const tableBody = document.getElementById('ledger-detailed-body');
        if (!tableBody) return;

        if (!records.length) {
            tableBody.innerHTML = '<tr><td colspan="9" style="text-align: center; color: var(--text-muted); padding: 36px 16px;">暂无大模型调用流水记录</td></tr>';
            return;
        }

        tableBody.innerHTML = records.map(function (record) {
            const isRecharge = Boolean(record.is_recharge || RECHARGE_SOURCES.has(record.source));
            const timeStr = formatTime(record);
            const idText = record.id ? String(record.id).slice(-8) : '--';

            if (isRecharge) {
                return `
                    <tr class="ledger-row-recharge">
                        <td class="ledger-id-cell">#${escapeHtml(idText)}</td>
                        <td>${renderCredentialCell(record)}</td>
                        <td><span class="badge" style="background: rgba(34, 197, 94, 0.12); color: #059669; font-weight: 600;">+ 额度充值</span></td>
                        <td class="ledger-muted-cell">--</td>
                        <td class="ledger-muted-cell">--</td>
                        <td class="ledger-muted-cell">--</td>
                        <td class="ledger-muted-cell">--</td>
                        <td class="ledger-cost-cell is-recharge">${escapeHtml(record.cost_formatted || '+$0.00')}</td>
                        <td class="ledger-time-cell">${escapeHtml(timeStr)}</td>
                    </tr>`;
            }

            const modelName = record.model || record.model_id || '通用模型';
            const inTok = formatNumber(record.input_tokens != null ? record.input_tokens : record.input);
            const cacheTok = formatNumber(record.cache_tokens != null ? record.cache_tokens : record.cached);
            const outTok = formatNumber(record.output_tokens != null ? record.output_tokens : record.output);

            return `
                <tr>
                    <td class="ledger-id-cell">#${escapeHtml(idText)}</td>
                    <td>${renderCredentialCell(record)}</td>
                    <td class="ledger-model-cell">${escapeHtml(modelName)}</td>
                    <td class="ledger-source-cell">${renderSourceCell(record)}</td>
                    <td class="font-mono">${inTok}</td>
                    <td class="font-mono ledger-muted-cell">${cacheTok}</td>
                    <td class="font-mono">${outTok}</td>
                    <td class="ledger-cost-cell">${escapeHtml(record.cost_formatted || '$0.0000')}
                        <button class="ledger-edit-cost" type="button" onclick="openLedgerPriceModal('${escapeHtml(String(record.id || ''))}')">改单价</button>
                    </td>
                    <td class="ledger-time-cell">${escapeHtml(timeStr)}</td>
                </tr>`;
        }).join('');
    }

    let tokenAlertData = null;

    function renderTokenAlerts(data) {
        tokenAlertData = data || {};
        const host = document.getElementById('token-alert-panel');
        if (!host) return;
        const settings = data.settings || {};
        const current = data.current || {};
        const alerts = Array.isArray(data.alerts) ? data.alerts : [];
        window.__tokenAlertData = data;
        const banner = alerts.length
            ? `<div class="token-alert-banner ${alerts.some(a => a.level === 'critical') ? 'critical' : 'warning'}">
                   ${alerts.map(a => `<div>${escapeHtml(a.message)}</div>`).join('')}
                   ${data.notified_today ? '<span class="token-alert-notified">今日已提醒</span>' : ''}
               </div>`
            : '<div class="token-alert-ok">余额和今日 Token 均在阈值内</div>';
        const limit = current.daily_limit ? formatNumber(current.daily_limit) : '样本不足';
        const balances = Array.isArray(current.balances) && current.balances.length
            ? current.balances
            : [{ currency: current.currency || 'USD', total: current.balance || 0, threshold: settings.balance_threshold || 0 }];
        const balanceRows = balances.map((item) => (
            `<div><span>${escapeHtml(item.currency || 'USD')} 余额</span><b>${escapeHtml(String(item.total ?? 0))} · 阈值 ${escapeHtml(String(item.threshold ?? 0))}</b></div>`
        )).join('');
        host.innerHTML = `
            ${banner}
            <div class="token-alert-metrics">
                ${balanceRows}
                <div><span>今日 Tokens</span><b>${formatNumber(current.today_tokens)}</b></div>
                <div><span>动态上限</span><b>${limit}</b></div>
                <div><span>参考天数</span><b>${current.samples || 0} 天</b></div>
            </div>
            <div class="token-alert-settings">
                <label>参考天数 <input id="token-alert-days" class="form-input" type="number" min="1" max="30" value="${escapeHtml(settings.lookback_days ?? 7)}"></label>
                <label>上限倍率 <input id="token-alert-multiplier" class="form-input" type="number" step="0.1" min="1" max="5" value="${escapeHtml(settings.limit_multiplier ?? 1.5)}"></label>
                <button class="btn btn-secondary" type="button" onclick="saveTokenAlertSettings()">保存预警</button>
                <span class="token-alert-hint">余额阈值在「添加额度」弹窗里改</span>
            </div>`;
    }

    async function loadTokenAlerts() {
        try {
            const resp = await fetch('/api/token/alerts', { cache: 'no-store' });
            const data = await resp.json();
            if (!resp.ok || !data.ok) throw new Error(data.error || `HTTP ${resp.status}`);
            renderTokenAlerts(data);
        } catch (e) {
            const host = document.getElementById('token-alert-panel');
            if (host) host.innerHTML = `<div class="token-alert-error">Token 预警读取失败：${escapeHtml(e.message || e)}</div>`;
        }
    }

    async function saveTokenAlertSettings() {
        const value = (id) => parseFloat(document.getElementById(id)?.value || '0');
        const body = {
            enabled: true,
            lookback_days: value('token-alert-days'),
            limit_multiplier: value('token-alert-multiplier'),
        };
        try {
            const resp = await fetch('/api/token/alerts/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            const data = await resp.json();
            if (!resp.ok || !data.ok) throw new Error(data.error || `HTTP ${resp.status}`);
            renderTokenAlerts(data);
        } catch (e) {
            const host = document.getElementById('token-alert-panel');
            if (host) host.innerHTML = `<div class="token-alert-error">保存失败：${escapeHtml(e.message || e)}</div>`;
        }
    }

    window.saveTokenAlertSettings = saveTokenAlertSettings;

    function ledgerRecordById(id) {
        return (window.__ledgerRecords || []).find(item => String(item.id) === String(id));
    }

    function ledgerNumber(value) {
        return parseFloat(String(value ?? '0').replace(/,/g, '')) || 0;
    }

    function openLedgerPriceModal(id) {
        const record = ledgerRecordById(id);
        const modal = document.getElementById('ledger-price-modal');
        if (!record || !modal) return;
        const input = Number(record.input_tokens || 0);
        const cache = Number(record.cache_tokens || 0);
        const output = Number(record.output_tokens || 0);
        const total = input + cache + output;
        const cost = Number(record.cost_usd || 0);
        const fallback = total > 0 ? cost * 1_000_000 / total : 0;
        const prices = record.unit_prices || {};
        document.getElementById('ledger-price-id').value = String(record.id || '');
        document.getElementById('ledger-price-model').innerText = record.model || '通用模型';
        document.getElementById('ledger-price-tokens').innerText = formatNumber(total);
        document.getElementById('ledger-price-input-tokens').innerText = formatNumber(input);
        document.getElementById('ledger-price-cache-tokens').innerText = formatNumber(cache);
        document.getElementById('ledger-price-output-tokens').innerText = formatNumber(output);
        document.getElementById('ledger-price-input').value = Number(prices.input ?? fallback).toFixed(4);
        document.getElementById('ledger-price-cache').value = Number(prices.cache ?? fallback).toFixed(4);
        document.getElementById('ledger-price-output').value = Number(prices.output ?? fallback).toFixed(4);
        document.getElementById('ledger-price-reason').value = '';
        updateLedgerPriceAmount();
        modal.style.display = 'flex';
    }

    function updateLedgerPriceAmount() {
        const inputTokens = ledgerNumber(document.getElementById('ledger-price-input-tokens')?.innerText);
        const cacheTokens = ledgerNumber(document.getElementById('ledger-price-cache-tokens')?.innerText);
        const outputTokens = ledgerNumber(document.getElementById('ledger-price-output-tokens')?.innerText);
        const inputPrice = Number(document.getElementById('ledger-price-input')?.value || 0);
        const cachePrice = Number(document.getElementById('ledger-price-cache')?.value || 0);
        const outputPrice = Number(document.getElementById('ledger-price-output')?.value || 0);
        const inputSubtotal = inputTokens * inputPrice / 1_000_000;
        const cacheSubtotal = cacheTokens * cachePrice / 1_000_000;
        const outputSubtotal = outputTokens * outputPrice / 1_000_000;
        const amount = inputSubtotal + cacheSubtotal + outputSubtotal;
        const set = (id, value) => {
            const el = document.getElementById(id);
            if (el) el.innerText = value.toFixed(4);
        };
        set('ledger-price-input-subtotal', inputSubtotal);
        set('ledger-price-cache-subtotal', cacheSubtotal);
        set('ledger-price-output-subtotal', outputSubtotal);
        set('ledger-price-amount', amount);
        return amount;
    }

    function closeLedgerPriceModal() {
        const modal = document.getElementById('ledger-price-modal');
        if (modal) modal.style.display = 'none';
    }

    async function saveLedgerPrice() {
        const id = document.getElementById('ledger-price-id')?.value;
        const amount = updateLedgerPriceAmount();
        if (!id || isNaN(amount) || amount < 0) {
            window.alert('单价或金额无效');
            return;
        }
        const reason = document.getElementById('ledger-price-reason')?.value || '';
        try {
            const resp = await fetch('/api/ledger/update', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ id, cost_usd: amount, reason }),
            });
            const data = await resp.json();
            if (!resp.ok || !data.ok) throw new Error(data.error || `HTTP ${resp.status}`);
            closeLedgerPriceModal();
            await loadDetailedLedger();
            if (typeof loadData === 'function') loadData();
        } catch (e) {
            window.alert('改价失败：' + (e.message || e));
        }
    }

    window.openLedgerPriceModal = openLedgerPriceModal;
    window.updateLedgerPriceAmount = updateLedgerPriceAmount;
    window.closeLedgerPriceModal = closeLedgerPriceModal;
    window.saveLedgerPrice = saveLedgerPrice;

    async function loadDetailedLedger() {
        loadTokenAlerts();
        const tableBody = document.getElementById('ledger-detailed-body');
        if (tableBody) {
            tableBody.innerHTML = '<tr><td colspan="9" style="text-align:center;color:var(--text-muted);padding:28px 16px;">正在读取流水...</td></tr>';
        }

        try {
            const resp = await fetch('/api/ledger', { cache: 'no-store' });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();

            const combined = data.combined || {};
            const today = data.today || {};
            const overall = data.overall || {};

            setText('ledger-total-count-display', `${formatNumber(combined.today_calls != null ? combined.today_calls : today.calls)} 笔`);
            setText('ledger-total-tokens-display', formatNumber(combined.today_tokens != null ? combined.today_tokens : today.total_tokens));
            setText('ledger-overall-count-display', `${formatNumber(combined.overall_calls != null ? combined.overall_calls : overall.calls)} 笔`);
            setText('ledger-overall-tokens-display', formatNumber(combined.overall_tokens != null ? combined.overall_tokens : overall.total_tokens));

            renderAccountingRows(data.accounting_groups);
            renderRecords(data.recent_ledger || data.recent_calls || []);
        } catch (e) {
            console.warn('Load detailed ledger failed:', e);
            if (tableBody) {
                tableBody.innerHTML = '<tr><td colspan="9" style="text-align: center; color: #ef4444; padding: 36px 16px;">流水加载失败，请刷新重试</td></tr>';
            }
        }
    }

    window.loadDetailedLedger = loadDetailedLedger;
})();
