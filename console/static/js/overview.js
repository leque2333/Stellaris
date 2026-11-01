// -*- coding: utf-8 -*-
// Stellaris - Dashboard & AI Resources & Vendor Themes Module

        function countUpNumber(el, targetVal, duration = 650, prefix = '', suffix = '', decimals = 0) {
            if (!el) return;
            if (el._countUpFrame) {
                cancelAnimationFrame(el._countUpFrame);
                el._countUpFrame = null;
            }
            if (el._countUpTimer) {
                clearTimeout(el._countUpTimer);
                el._countUpTimer = null;
            }
            const startVal = 0;
            const endVal = parseFloat(targetVal) || 0;
            el.innerText = prefix + (0).toFixed(decimals) + suffix;
            if (endVal === 0 && startVal === 0) {
                return;
            }
            let startTime = null;
            function update(now) {
                if (startTime === null) startTime = now;
                const elapsed = now - startTime;
                const progress = Math.min(elapsed / duration, 1);
                // easeOutExpo 缓动算法
                const ease = progress === 1 ? 1 : 1 - Math.pow(2, -10 * progress);
                const current = startVal + (endVal - startVal) * ease;
                el.innerText = prefix + current.toFixed(decimals) + suffix;
                if (progress < 1) {
                    el._countUpFrame = requestAnimationFrame(update);
                } else {
                    el._countUpFrame = null;
                    el.innerText = prefix + endVal.toFixed(decimals) + suffix;
                }
            }
            // 先让 0 稳定显示一小段时间，避免浏览器首帧直接跳到中间值
            el._countUpTimer = setTimeout(() => {
                el._countUpTimer = null;
                el._countUpFrame = requestAnimationFrame(update);
            }, 120);
        }

        // ==========================================
        // 2. 币种自动识别与国产副币种标注引擎
        // ==========================================
        function detectCredentialCurrency(cred) {
            if (!cred) return 'USD';
            if (cred.currency) {
                const c = String(cred.currency).toUpperCase();
                if (c === 'CNY' || c === 'RMB') return 'CNY';
                if (c === 'USD') return 'USD';
            }
            const allStr = `${cred.provider || ''} ${cred.model || ''} ${cred.name || ''}`.toLowerCase();
            const cnyKeywords = ['deepseek', '千问', 'qwen', '通义', 'glm', '智谱', 'baidu', '文心', 'ernie', 'minimax', 'moonshot', 'kimi', '百炼', '国产'];
            if (cnyKeywords.some(k => allStr.includes(k))) {
                return 'CNY';
            }
            return 'USD';
        }

        function updateCurrencyAndBalances() {
            const mainEl = document.getElementById('hero-credits-balance');
            if (!mainEl) return;
            const listEl = document.getElementById('hero-secondary-list');
            const unitEl = document.getElementById('hero-primary-unit');
            const dashCredits = (window.lastDashboardData && window.lastDashboardData.tokens && window.lastDashboardData.tokens.credits) || null;
            let balances = (dashCredits && Array.isArray(dashCredits.balances)) ? dashCredits.balances.slice() : [];

            // 兼容旧接口：没有 balances 时退回单主余额
            if (!balances.length) {
                const cfg = globalModelsConfig || {};
                const masterList = cfg.master_credentials || [];
                const activeMaster = masterList.find(c => c.is_active !== false && (c.model || c.api_key)) || masterList[0];
                const masterCurrency = detectCredentialCurrency(activeMaster);
                const symbol = masterCurrency === 'CNY' ? '¥' : '$';
                const value = dashCredits && dashCredits.balance !== undefined
                    ? parseFloat(dashCredits.balance)
                    : (cfg.credits && cfg.credits.balance !== undefined ? parseFloat(cfg.credits.balance) : 0.0);
                balances = [{
                    currency: masterCurrency,
                    symbol: symbol,
                    total: value,
                    total_formatted: `${value < 0 ? '-' : ''}${symbol}${Math.abs(value).toFixed(2)}`,
                    is_primary: true,
                }];
            }

            // 主控币种必须坐主位
            const primaryIndex = balances.findIndex(item => item.is_primary);
            if (primaryIndex > 0) {
                const primaryItem = balances[primaryIndex];
                balances = [primaryItem].concat(balances.filter((_, index) => index !== primaryIndex));
            }
            const primary = balances[0];
            const secondary = balances.slice(1, 4);
            const animateBalance = !window.__balanceRendered;
            const renderBalance = (el, value, symbol) => {
                if (!el) return;
                const numeric = Number(value) || 0;
                const prefix = `${numeric < 0 ? '-' : ''}${symbol || ''}`;
                if (animateBalance && typeof countUpNumber === 'function') {
                    countUpNumber(el, Math.abs(numeric), 700, prefix, '', 2);
                } else {
                    // 数字还在滚动时不要直接覆盖成最终值
                    if (el._countUpFrame || el._countUpTimer) return;
                    el.innerText = `${prefix}${Math.abs(numeric).toFixed(2)}`;
                }
            };

            renderBalance(mainEl, primary.total, primary.symbol);
            if (unitEl) unitEl.innerText = `主控 ${primary.currency || 'USD'}`;

            if (listEl) {
                listEl.innerHTML = secondary.map(function (item) {
                    return `
                        <button type="button" class="hero-secondary-item" data-currency="${item.currency}" onclick="openRechargeModal('secondary','${item.currency}')" title="点击添加 ${item.currency} 金额">
                            <span class="hero-secondary-code">${item.currency}</span>
                            <span class="hero-secondary-amount" data-currency="${item.currency}"></span>
                        </button>`;
                }).join('');

                secondary.forEach(function (item) {
                    const el = listEl.querySelector(`.hero-secondary-amount[data-currency="${item.currency}"]`);
                    if (el) renderBalance(el, item.total, item.symbol);
                });
            }
            window.__balanceRendered = true;
        }

        // ==========================================
        // 3. 仪表盘数字数值平滑渐增动画触发器
        // ==========================================
        function animateDashboardNumbers() {
            // 1. 触发主额度与副币种金额数值渐增
            updateCurrencyAndBalances();
            // 2. 触发右侧 4 项统计指标数值渐增
            const tokensData = (window.lastDashboardData && window.lastDashboardData.tokens) || {};
            const today = tokensData.today || {};
            const overall = tokensData.overall || {};
            const todayCalls = today.calls || 0;
            const todayTokens = today.total_tokens || 0;

            countUpNumber(document.getElementById('m-today-calls'), todayCalls, 900, '', '', 0);
            countUpNumber(document.getElementById('m-today-tokens-sum'), todayTokens, 1000, '', '', 0);
            window.__tokenAnimationUntil = performance.now() + 1700;
        }

        function renderMonthlyHeatmap(activity) {
            const grid = document.getElementById('month-heatmap-grid');
            if (!grid || !activity || !Array.isArray(activity.days)) return;

            const weekdays = ['一', '二', '三', '四', '五', '六', '日'];
            let html = weekdays.map(function (name) {
                return `<div class="heat-weekday">${name}</div>`;
            }).join('');

            const days = activity.days;
            const leading = days.length ? Number(days[0].weekday || 0) : 0;
            for (let i = 0; i < leading; i += 1) {
                html += '<div class="heat-cell is-empty"></div>';
            }

            days.forEach(function (day) {
                const calls = Number(day.calls || 0).toLocaleString();
                const tokens = Number(day.tokens || 0).toLocaleString();
                const score = Number(day.score || 0).toLocaleString();
                const title = `${activity.year}年${activity.month}月${day.day}日\n${calls} 次调用 · ${tokens} Tokens\n综合分 ${score}（Tokens + 调用次数 × 5000）`;
                html += `<div class="heat-cell heat-level-${Number(day.level || 0)}" title="${title}"><span>${day.day}</span></div>`;
            });

            const trailing = (7 - ((leading + days.length) % 7)) % 7;
            for (let i = 0; i < trailing; i += 1) {
                html += '<div class="heat-cell is-empty"></div>';
            }

            grid.innerHTML = html;
            const monthEl = document.getElementById('month-heatmap-month');
            if (monthEl) monthEl.textContent = `${activity.year}年${activity.month}月`;
        }

        async function fetchTokenStats(animate = true) {
            if (window.__tokenStatsFetching) return;
            window.__tokenStatsFetching = true;
            try {
                const resp = await fetch('/api/tokens', { cache: 'no-store' });
                if (!resp.ok) return;
                const tokens = await resp.json();
                window.lastDashboardData = Object.assign({}, window.lastDashboardData || {}, { tokens });
                window.__tokenStatsLoaded = true;

                const today = tokens.today || {};
                const overall = tokens.overall || {};
                const credits = tokens.credits || {};
                renderMonthlyHeatmap(tokens.daily_activity);
                const setText = (id, value) => {
                    const el = document.getElementById(id);
                    if (el) el.innerText = value;
                };
                setText('hero-today-cost', credits.today_cost_formatted || '$0.0000');
                setText('hero-today-tokens', `${(today.total_tokens || 0).toLocaleString()} Tokens`);
                setText('hero-total-cost', credits.overall_cost_formatted || '$0.0000');
                setText('hero-total-tokens', `${(overall.total_tokens || 0).toLocaleString()} Tokens`);
                setText('m-today-tokens-breakdown', `In: ${(today.input_tokens || 0).toLocaleString()} · Out: ${(today.output_tokens || 0).toLocaleString()} · Cache: ${(today.cached_tokens || 0).toLocaleString()}`);
                setText('m-today-cost-value', credits.today_cost_formatted || '$0.0000');
                const activeMasterCred = ((globalModelsConfig && globalModelsConfig.master_credentials) || []).find(c => c.is_active !== false);
                setText('m-today-cost-currency', (activeMasterCred && activeMasterCred.currency) || credits.currency || 'USD');
                setText('m-active-model-tag', (globalModelsConfig && globalModelsConfig.active_model) || 'gemini-3.8-flash');

                if (animate && !window.__dashboardCounted) {
                    window.__dashboardCounted = true;
                    // 让真实余额数据到达后重新触发一次数字滚动，避免被早前的默认值渲染吃掉
                    window.__balanceRendered = false;
                    animateDashboardNumbers();
                } else {
                    if (typeof updateCurrencyAndBalances === 'function') updateCurrencyAndBalances();
                    setText('m-today-calls', (today.calls || 0).toLocaleString());
                    setText('m-today-tokens-sum', (today.total_tokens || 0).toLocaleString());
                }
                if (!window.__tokenStatsSettled) {
                    window.__tokenStatsSettled = true;
                    const settledCalls = today.calls || 0;
                    const settledTokens = today.total_tokens || 0;
                    setTimeout(() => {
                        setText('m-today-calls', settledCalls.toLocaleString());
                        setText('m-today-tokens-sum', settledTokens.toLocaleString());
                        if (typeof updateCurrencyAndBalances === 'function') updateCurrencyAndBalances();
                    }, 1800);
                }
            } catch (e) {
                console.warn('Token stats load failed:', e);
            } finally {
                window.__tokenStatsFetching = false;
            }
        }
        window.fetchTokenStats = fetchTokenStats;

        // ==========================================
        // 4. 实时 AI 服务与硬件资源占用监控引擎
        // ==========================================
        let _overviewResourceTimer = null;

        async function fetchSystemResources() {
            try {
                const resp = await fetch('/api/system/resources');
                if (!resp.ok) return;
                const data = await resp.json();
                renderAIResources(data);
            } catch (e) {
                console.warn('Resource polling failed:', e);
            }
        }

                function renderAIResources(res) {
            if (!res) return;

            const ai = res.ai_metrics || {};

            // 1. GPU 渲染 (AI 显卡与模型显存池 - 突出百分比)
            const gpuNameEl = document.getElementById('hw-gpu-name');
            const gpuUtilEl = document.getElementById('hw-gpu-util-num');
            const gpuBarEl = document.getElementById('hw-gpu-bar');
            const gpuTempEl = document.getElementById('hw-gpu-temp-info');
            const gpuFootEl = document.getElementById('hw-gpu-mem-foot');

            if (res.gpu) {
                // 优先展示 GPU 综合/显存百分比
                const gpuPercent = res.gpu.memory_percent !== undefined ? res.gpu.memory_percent : (res.gpu.util_percent || 0);
                if (gpuNameEl) gpuNameEl.innerText = res.gpu.name || 'NVIDIA GPU';
                if (gpuUtilEl) countUpNumber(gpuUtilEl, gpuPercent, 450, '', '', 1);
                if (gpuBarEl) gpuBarEl.style.width = Math.min(gpuPercent, 100) + '%';
                if (gpuTempEl) gpuTempEl.innerText = (res.gpu.temperature !== null ? `${res.gpu.temperature}°C` : '正常');
                if (gpuFootEl) gpuFootEl.innerText = `显存: ${res.gpu.memory_used_mb} MB / ${res.gpu.memory_total_mb} MB`;
            } else {
                if (gpuNameEl) gpuNameEl.innerText = '集成图形单元 / NPU';
                if (gpuUtilEl) gpuUtilEl.innerText = '0';
                if (gpuBarEl) gpuBarEl.style.width = '0%';
                if (gpuTempEl) gpuTempEl.innerText = '常温';
                if (gpuFootEl) gpuFootEl.innerText = '共享系统内存';
            }

            // 2. CPU 渲染 (仅显示 AI 核心专属 CPU 算力百分比)
            const cpuUtilEl = document.getElementById('hw-cpu-util-num');
            const cpuBarEl = document.getElementById('hw-cpu-bar');
            const cpuFootEl = document.getElementById('hw-cpu-cores-foot');
            const cpuBadgeEl = document.getElementById('hw-cpu-badge');

            const aiCpuPercent = (ai.cpu_percent !== undefined) ? ai.cpu_percent : (res.cpu ? res.cpu.percent : 0.0);
            const aiSvcCount = ai.service_count || (res.services ? res.services.length : 0);

            if (cpuUtilEl) countUpNumber(cpuUtilEl, aiCpuPercent, 450, '', '', 1);
            if (cpuBarEl) cpuBarEl.style.width = Math.min(aiCpuPercent * 5, 100) + '%';
            if (cpuFootEl) cpuFootEl.innerText = `${res.cpu && res.cpu.cores ? res.cpu.cores : 16} 逻辑核心 · ${aiSvcCount} 个 AI 进程并发`;
            if (cpuBadgeEl) cpuBadgeEl.innerText = `CPU 负载`;

            // 3. 内存 RAM 渲染 (仅显示所有 AI 服务的专属物理内存占整机的百分比)
            const ramUtilEl = document.getElementById('hw-ram-util-num');
            const ramBarEl = document.getElementById('hw-ram-bar');
            const ramSubEl = document.getElementById('hw-ram-used-gb');
            const ramFootEl = document.getElementById('hw-ram-foot');

            const aiMemMb = (ai.memory_mb !== undefined) ? ai.memory_mb : (res.memory ? (res.memory.ai_memory_mb || 0) : 0);
            const ramPercent = (res.memory && res.memory.percent !== undefined) 
                ? res.memory.percent 
                : Math.min(parseFloat(((aiMemMb / (32 * 1024)) * 100).toFixed(1)), 100.0);

            if (ramUtilEl) countUpNumber(ramUtilEl, ramPercent, 450, '', '', 1);
            if (ramBarEl) {
                ramBarEl.style.width = Math.min(Math.max(ramPercent, 2), 100) + '%';
            }
            if (ramSubEl) ramSubEl.innerText = `整机占比`;
            const totalRamGb = (res.memory && res.memory.total_gb) ? res.memory.total_gb : 32.0;
            if (ramFootEl) ramFootEl.innerText = `AI 占用: ${aiMemMb} MB / ${totalRamGb} GB`;
        }

        // ==========================================
        // 5. 独立详细账单流水页面数据加载与渲染
        // ==========================================


        function detectActiveMasterProvider() {
            const masterList = (globalModelsConfig && globalModelsConfig.master_credentials) || [];
            const activeMaster = masterList.find(c => c.is_active && (c.model || c.api_key)) || masterList[0];
            if (!activeMaster) {
                return { key: 'gemini', name: 'Google Gemini', isAuto: true };
            }

            const prov = (activeMaster.provider || '').toLowerCase();
            const model = (activeMaster.model || '').toLowerCase();
            const url = (activeMaster.base_url || '').toLowerCase();
            const name = (activeMaster.name || '').toLowerCase();
            const allStr = `${prov} ${model} ${url} ${name}`;

            if (allStr.includes('gemini') || allStr.includes('google') || allStr.includes('generativelanguage')) {
                return { key: 'gemini', name: activeMaster.provider || 'Google Gemini', isAuto: true };
            } else if (allStr.includes('openai') || allStr.includes('gpt') || allStr.includes('chatgpt')) {
                return { key: 'openai', name: activeMaster.provider || 'OpenAI / GPT', isAuto: true };
            } else if (allStr.includes('anthropic') || allStr.includes('claude')) {
                return { key: 'claude', name: activeMaster.provider || 'Anthropic Claude', isAuto: true };
            } else if (allStr.includes('deepseek')) {
                return { key: 'deepseek', name: activeMaster.provider || 'DeepSeek', isAuto: true };
            } else {
                return { key: 'other', name: activeMaster.provider || '其他厂商', isAuto: true };
            }
        }

        function applyHeroStripTheme() {
            const heroEl = document.querySelector('.balance-hero-strip');
            if (!heroEl) return;

            const detected = detectActiveMasterProvider();
            const savedMode = localStorage.getItem('hero_theme_mode') || 'auto';

            let targetTheme = 'gemini';
            if (savedMode === 'auto') {
                targetTheme = (detected.key === 'other') ? 'white' : detected.key;
            } else {
                targetTheme = savedMode;
            }

            // 更新容器类名（保留 hero-card-v2 布局类，避免主题切换把新卡片布局冲掉）
            heroEl.className = `balance-hero-strip hero-card-v2 theme-${targetTheme}`;

            // 更新设置页面的下拉菜单与检测提示
            const selectEl = document.getElementById('setting-hero-theme-mode');
            if (selectEl) {
                selectEl.value = savedMode;
            }

            const badgeEl = document.getElementById('hero-theme-detected-badge');
            if (badgeEl) {
                const labelMap = {
                    'gemini': 'Google Gemini (电光蓝彩)',
                    'openai': 'OpenAI / GPT (翡翠黑绿)',
                    'claude': 'Anthropic Claude (陶土暖橙)',
                    'deepseek': 'DeepSeek (赛博冰蓝)',
                    'other': '其他厂商 (默认纯白)',
                    'white': '默认极简纯白'
                };
                if (savedMode === 'auto') {
                    badgeEl.innerText = `主凭证检测：${detected.name} ➔ 生效主题：${labelMap[targetTheme] || targetTheme}`;
                } else {
                    badgeEl.innerText = `手动锁定：${labelMap[targetTheme] || targetTheme} (主凭证为: ${detected.name})`;
                }
            }
        }

        function changeHeroThemeMode(mode) {
            localStorage.setItem('hero_theme_mode', mode);
            applyHeroStripTheme();
            if (typeof showToast === 'function') {
                showToast('已更新看板默认取色模式');
            }
        }

        function updateTopActiveModelDisplay() {
            if (!globalModelsConfig) return;
            
            let activeModelName = globalModelsConfig.active_model || 'gemini-3.8-flash';
            let activeProvider = 'Google';
            let activeReasoning = globalModelsConfig.reasoning_effort || 'medium';
            let inPrice = 0.825;
            let cachePrice = 0.0825;
            let outPrice = 4.125;

            const masterList = globalModelsConfig.master_credentials || [];
            const activeMaster = masterList.find(c => c.is_active && (c.model || c.api_key)) || masterList[0];
            if (activeMaster) {
                activeModelName = activeMaster.model || activeModelName;
                activeProvider = activeMaster.provider || activeProvider;
                activeReasoning = activeMaster.reasoning_effort || activeReasoning;
                inPrice = activeMaster.input_price !== undefined ? activeMaster.input_price : inPrice;
                cachePrice = activeMaster.cache_price !== undefined ? activeMaster.cache_price : cachePrice;
                outPrice = activeMaster.output_price !== undefined ? activeMaster.output_price : outPrice;
            }

            syncActiveModelToAllDisplays(activeModelName, activeProvider, activeReasoning);
            applyHeroStripTheme();

            // 同步顶部模型条单价展示
            const inEl = document.getElementById('strip-rate-in');
            if (inEl) inEl.innerText = `$${parseFloat(inPrice).toFixed(3)}/MTok`;
            const cacheEl = document.getElementById('strip-rate-cache');
            if (cacheEl) cacheEl.innerText = `$${parseFloat(cachePrice).toFixed(4)}/MTok`;
            const outEl = document.getElementById('strip-rate-out');
            if (outEl) outEl.innerText = `$${parseFloat(outPrice).toFixed(3)}/MTok`;
        }



window.countUpNumber = countUpNumber;
window.animateDashboardNumbers = animateDashboardNumbers;
window.fetchSystemResources = fetchSystemResources;
window.renderAIResources = renderAIResources;
window.detectActiveMasterProvider = detectActiveMasterProvider;
window.applyHeroStripTheme = applyHeroStripTheme;
window.changeHeroThemeMode = changeHeroThemeMode;
window.updateTopActiveModelDisplay = updateTopActiveModelDisplay;

// ==========================================
// 充值 Modal 控制器
// ==========================================

        let rechargeTarget = 'master';
        let rechargeCurrency = '';

        function openRechargeModal(target = 'master', currency = '') {
            const modal = document.getElementById('recharge-modal');
            if (!modal) return;
            rechargeTarget = target === 'secondary' ? 'secondary' : 'master';
            const title = document.getElementById('recharge-modal-title');
            const label = document.getElementById('recharge-amount-label');
            const balances = (window.lastDashboardData?.tokens?.credits?.balances) || [];
            if (rechargeTarget === 'secondary') {
                rechargeCurrency = currency
                    || (balances[1] && balances[1].currency)
                    || detectCredentialCurrency((globalModelsConfig?.function_credentials || []).find(fc => fc.is_active !== false) || (globalModelsConfig?.function_credentials || [])[0]);
            } else {
                rechargeCurrency = (balances[0] && balances[0].currency)
                    || detectCredentialCurrency((globalModelsConfig?.master_credentials || []).find(c => c.is_active !== false) || (globalModelsConfig?.master_credentials || [])[0]);
            }
            if (title) title.innerText = rechargeTarget === 'secondary' ? '添加金额' : '添加额度';
            if (label) label.innerText = `充值金额（${rechargeCurrency}）`;
            modal.style.display = 'flex';
            const inp = document.getElementById('recharge-amount-input');
            if (inp) {
                inp.value = '';
                inp.focus();
            }
            const res = document.getElementById('recharge-result');
            if (res) res.innerText = '';
            loadRechargeThreshold(rechargeCurrency);
        }

        async function loadRechargeThreshold(currency) {
            const label = document.getElementById('recharge-threshold-label');
            const input = document.getElementById('recharge-threshold-input');
            const result = document.getElementById('recharge-threshold-result');
            if (label) label.innerText = `余额提醒阈值（${currency || 'USD'}）`;
            if (result) result.innerText = '';
            let data = window.__tokenAlertData;
            if (!data) {
                try {
                    const resp = await fetch('/api/token/alerts', { cache: 'no-store' });
                    data = await resp.json();
                    if (data && data.ok) window.__tokenAlertData = data;
                } catch (e) {
                    data = null;
                }
            }
            if (!input) return;
            const rows = (data && data.current && data.current.balances) || [];
            const row = rows.find(item => String(item.currency || '').toUpperCase() === String(currency || '').toUpperCase());
            const fallback = data && data.settings ? data.settings.balance_threshold : 5;
            input.value = row ? row.threshold : fallback;
        }

        async function saveRechargeThreshold() {
            const input = document.getElementById('recharge-threshold-input');
            const result = document.getElementById('recharge-threshold-result');
            const value = parseFloat(input?.value || '0');
            if (!input || isNaN(value) || value < 0) {
                if (result) result.innerText = '请输入有效的阈值';
                return;
            }
            const currency = String(rechargeCurrency || 'USD').toUpperCase();
            const data = window.__tokenAlertData || {};
            const thresholds = Object.assign({}, (data.settings && data.settings.balance_thresholds) || {});
            thresholds[currency] = value;
            try {
                const resp = await fetch('/api/token/alerts/settings', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ balance_thresholds: thresholds }),
                });
                const payload = await resp.json();
                if (!resp.ok || !payload.ok) throw new Error(payload.error || `HTTP ${resp.status}`);
                window.__tokenAlertData = payload;
                if (result) {
                    result.style.color = '#059669';
                    result.innerText = `已保存：${currency} 低于 ${value} 时提醒`;
                }
            } catch (e) {
                if (result) {
                    result.style.color = '#dc2626';
                    result.innerText = '保存失败：' + (e.message || e);
                }
            }
        }

        window.loadRechargeThreshold = loadRechargeThreshold;
        window.saveRechargeThreshold = saveRechargeThreshold;

        function closeRechargeModal(event) {
            // 仅点击遮罩层本身时关闭（内容区 stopPropagation 已处理）
            const modal = document.getElementById('recharge-modal');
            if (modal && (!event || event.target === modal)) {
                modal.style.display = 'none';
            }
        }

        function setRechargePreset(amount) {
            const inp = document.getElementById('recharge-amount-input');
            if (inp) inp.value = amount;
        }

        async function submitRecharge() {
            const inp = document.getElementById('recharge-amount-input');
            const resEl = document.getElementById('recharge-result');
            if (!inp || !resEl) return;

            const amountStr = inp.value.trim();
            const amount = parseFloat(amountStr);
            if (!amountStr || isNaN(amount) || amount <= 0) {
                resEl.style.color = '#ef4444';
                resEl.innerText = '请输入有效的充值金额（大于 0）';
                return;
            }

            resEl.style.color = '#64748b';
            resEl.innerText = '正在处理…';

            if (rechargeTarget === 'secondary') {
                const targetCurrency = rechargeCurrency || detectCredentialCurrency(
                    ((globalModelsConfig && globalModelsConfig.function_credentials) || []).find(item => item.is_active !== false)
                    || ((globalModelsConfig && globalModelsConfig.function_credentials) || [])[0]
                );
                const allCredentials = []
                    .concat((globalModelsConfig && globalModelsConfig.master_credentials) || [])
                    .concat((globalModelsConfig && globalModelsConfig.function_credentials) || []);
                const credentialCurrency = (item) => {
                    const raw = String((item && item.currency) || '').trim().toUpperCase();
                    if (raw === 'RMB') return 'CNY';
                    if (raw) return raw;
                    return detectCredentialCurrency(item);
                };
                const candidates = allCredentials.filter(item => credentialCurrency(item) === targetCurrency);
                const cred = candidates.find(item => item.is_active !== false) || candidates[0];
                if (!cred) {
                    resEl.style.color = '#ef4444';
                    resEl.innerText = `没有可充值的 ${targetCurrency} 凭证`;
                    return;
                }
                const symbol = targetCurrency === 'CNY' ? '¥'
                    : targetCurrency === 'USD' ? '$'
                    : targetCurrency === 'EUR' ? '€'
                    : `${targetCurrency} `;
                const oldBalance = parseFloat(cred.balance !== undefined ? cred.balance : (cred.allocated_quota || 0)) || 0;
                cred.balance = parseFloat((oldBalance + amount).toFixed(4));
                try {
                    await saveCredentialsPoolConfig(true);
                    if (typeof fetchTokenStats === 'function') await fetchTokenStats(false);
                    updateCurrencyAndBalances();
                    resEl.style.color = '#059669';
                    resEl.innerText = `充值成功！${targetCurrency} 当前余额: ${symbol}${cred.balance.toFixed(2)}`;
                    setTimeout(() => closeRechargeModal(), 1500);
                } catch (e) {
                    resEl.style.color = '#ef4444';
                    resEl.innerText = `操作失败：${e.message || e}`;
                }
                return;
            }

            try {
                // 先读取当前余额
                const cfgResp = await fetch('/api/ledger');
                if (!cfgResp.ok) throw new Error('读取余额失败');
                const cfgData = await cfgResp.json();
                const oldBalance = parseFloat((cfgData.credits && cfgData.credits.balance) || 0);
                const newBalance = parseFloat((oldBalance + amount).toFixed(4));

                // 调用 /api/credits 写入充值，并让后端自动记账
                const resp = await fetch('/api/credits', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        balance: newBalance,
                        top_up_amount: amount
                    })
                });
                if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
                const result = await resp.json();

                if (result.success) {
                    const newBal = result.credits && result.credits.balance !== undefined
                        ? parseFloat(result.credits.balance).toFixed(2)
                        : newBalance.toFixed(2);
                    const primarySymbol = (window.lastDashboardData?.tokens?.credits?.balances?.[0]?.symbol) || '$';
                    const newBalNum = Number(newBal) || 0;
                    const signedPrefix = `${newBalNum < 0 ? '-' : ''}${primarySymbol}`;
                    resEl.style.color = '#059669';
                    resEl.innerText = `充值成功！当前余额: ${signedPrefix}${Math.abs(newBalNum).toFixed(2)}`;

                    // 更新主页余额显示
                    const balEl = document.getElementById('hero-credits-balance');
                    const symbol = (window.lastDashboardData?.tokens?.credits?.balances?.[0]?.symbol)
                        || (balEl && balEl.innerText.includes('¥') ? '¥' : '$');
                    const balancePrefix = `${newBalNum < 0 ? '-' : ''}${symbol}`;
                    if (balEl && typeof countUpNumber === 'function') {
                        countUpNumber(balEl, Math.abs(newBalNum), 500, balancePrefix, '', 2);
                    } else if (balEl) {
                        balEl.innerText = `${balancePrefix}${Math.abs(newBalNum).toFixed(2)}`;
                    }

                    // 更新顶部配额显示
                    const quotaEl = document.getElementById('hero-granted-quota');
                    if (quotaEl && result.credits && result.credits.total_granted !== undefined) {
                        quotaEl.innerText = symbol + parseFloat(result.credits.total_granted).toFixed(2);
                    }

                    // 重新拉取余额，刷新主/副金额的合计
                    if (typeof fetchTokenStats === 'function') fetchTokenStats(false);

                    // 刷新流水列表（若当前在流水页）
                    const tab = document.getElementById('tab-ledger');
                    if (tab && tab.classList.contains('active') && typeof loadDetailedLedger === 'function') {
                        setTimeout(loadDetailedLedger, 500);
                    }

                    // 2 秒后自动关闭
                    setTimeout(() => {
                        const modal = document.getElementById('recharge-modal');
                        if (modal) modal.style.display = 'none';
                    }, 2000);
                } else {
                    resEl.style.color = '#ef4444';
                    resEl.innerText = `充值失败：${result.error || '未知错误'}`;
                }
            } catch (e) {
                resEl.style.color = '#ef4444';
                resEl.innerText = `操作失败：${e.message}`;
            }
        }

window.openRechargeModal = openRechargeModal;
window.closeRechargeModal = closeRechargeModal;
window.setRechargePreset = setRechargePreset;
window.submitRecharge = submitRecharge;
window.syncActiveModelToAllDisplays = window.syncActiveModelToAllDisplays || function() {};

