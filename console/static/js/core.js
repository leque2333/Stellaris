// -*- coding: utf-8 -*-
// Stellaris - Core Engine (State, i18n, Navigation, Polling)

// 核心补丁：全局拦截所有 fetch，统一带上同源 Cookie（会话只走 HttpOnly Cookie，
// 不再从 localStorage 取 Token，避免任一 XSS 直接接管控制台）。
        (function() {
            const originalFetch = window.fetch;
            window.fetch = function(url, options) {
                options = options || {};
                options.credentials = options.credentials || 'include';
                return originalFetch.apply(this, [url, options]).then(function(resp) {
                    // 登录态失效/首次改密：统一回登录页，别让页面在半登录状态下空转
                    if (resp && (resp.status === 401 || resp.status === 403)) {
                        const path = window.location.pathname || '';
                        if (!/^\/login|^\/change-password/.test(path)) {
                            resp.clone().json().then(function(data) {
                                if (data && data.must_change_password) {
                                    window.location.href = '/change-password';
                                } else if (data && data.require_login) {
                                    window.location.href = '/login';
                                }
                            }).catch(function() {});
                        }
                    }
                    return resp;
                });
            };
        })();

        var currentLang = localStorage.getItem('hub_lang') || 'zh-CN';
        var globalConfig = null;
        var globalModelsConfig = null;
                var isInitialLoaded = false;
        var resourceMonitorTimer = null;
        var personaRules = null;
        window.currentLang = currentLang;
        window.globalConfig = globalConfig;
        window.globalModelsConfig = globalModelsConfig;
                window.resourceMonitorTimer = resourceMonitorTimer;

        function escapeHtml(str) {
            if (str === null || str === undefined) return '';
            return String(str)
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#39;');
        }

        var i18n = window.i18n = {
            'zh-CN': {
                brand_title: 'Stellaris',
                nav_core: '核心功能',
                nav_overview: '仪表盘',
                nav_memory: '时间轴',
                nav_persona: '人格设定',
                nav_ledger: '账单流水',
                title_ledger: '账单流水与消费明细',
                nav_settings: '设置',
                nav_logout: '退出',
                status_online: '运行中',
                title_overview: '仪表盘',
                title_timeline: '时间轴',
                title_persona: '人格设定',
                title_settings: '系统设置',
                type_core: '独立架构',
                label_reasoning_effort: '思考强度',
                label_reasoning_effort_short: '思考:',
                hero_credits_label: '账户余额',
                hero_today_cost: '今日支出',
                hero_total_cost: '累计支出',
                hero_granted_quota: '初始总配额',
                btn_manage_credits: '设置额度 / 充值',
                btn_refresh: '刷新数据',
                btn_view_all: '全部 ➔',
                stat_today_calls: '今日调用',
                stat_today_tokens: '今日 Token 吞吐',
                stat_total_calls: '累计调用总数',
                stat_total_tokens: '累计消耗 Token',
                active_model_label: '当前模型',
                btn_change_model: '切换 ➔',
                overview_activity_title: '调用明细与账本流水',
                overview_activity_desc: '大模型实时调用记录、Token 消耗与单次费用核算',
                th_cost: '费用',
                modal_credits_title: '账户额度 / 充值配额',
                modal_credits_desc: '设置初始配额或模拟充值金额 ($ USD)，实时流水将从中核算扣减剩余余额。',
                modal_credits_input: '配额总额 ($ USD)',
                modal_credits_btn: '保存配额',
                btn_cancel: '取消',
                timeline_title: '时间轴',
                timeline_desc: '按插件记录交互；群聊标群名，私聊标对方昵称',
                persona_title: '系统提示词在线配置',
                persona_desc: '纯架构模式：提示词默认为空，由使用者自由填写与保存生效',
                btn_save_persona: '保存并生效',
                settings_model_title: '模型配置',
                settings_active_model: '选择模型',
                label_choose_preset: '预设模板',
                btn_sync_upstream: '同步在线模型源',
                btn_save_settings: '保存设置',
                subtab_models: '模型与定价',
                subtab_pool: 'API 轮换池',
                subtab_general: '常规设置',
                th_model_id: '模型 ID',
                th_model_name: '模型名称',
                th_provider: '厂商',
                th_input_price: '输入单价 $/MTok',
                th_output_price: '输出单价 $/MTok',
                th_cache_price: '缓存单价 $/MTok',
                api_pool_title: 'API 轮换池',
                btn_add_key: '+ 添加密钥',
                th_key_name: '名称',
                th_key_provider: '厂商',
                th_key_val: 'API 密钥',
                th_key_base: '代理端点',
                th_key_status: '状态',
                th_action: '操作',
                settings_lang_title: '常规设置',
                settings_lang_label: '界面语言',
                settings_port_label: '服务端口',
                settings_info_title: '架构信息',
                ext_title: '模块与工具生态',
                ext_desc: '非本体参数，仅在使用者自行接入特定外部扩展插件时生效',
                btn_save_ext: '保存扩展配置',
                ext_daily_limit: '单日评论上限',
                ext_session_limit: '单次会话上限',
                ext_interval: '安全间隔 秒',
                ext_body_len: '评论字数限制',
                ext_master_uid: '关联 UID',
                ext_master_qq: '关联 QQ',
                ext_keywords: '关键词过滤池',
                ext_target_pool: '标签过滤池',
                toast_saved: '配置已更新保存',
                toast_prompt_saved: '提示词已更新并热重载生效',
                empty_timeline: '暂无时间轴事件',
                empty_snippet: '暂无近期切片'
            },
            'en-US': {
                brand_title: 'Stellaris',
                nav_core: 'Core Functions',
                nav_overview: 'Overview',
                nav_memory: 'Timeline',
                nav_persona: 'Persona',
                nav_ledger: 'Ledger',
                title_ledger: 'Billing & Token Ledger',
                nav_settings: 'Settings',
                nav_logout: 'Sign out',
                status_online: 'Live',
                title_overview: 'Overview Dashboard',
                title_timeline: 'Timeline',
                title_persona: 'Persona Config',
                title_settings: 'Settings',
                type_core: 'Core Engine',
                label_reasoning_effort: 'Reasoning Effort',
                label_reasoning_effort_short: 'Thinking:',
                hero_credits_label: 'Credit Balance',
                hero_today_cost: 'Today Spend',
                hero_total_cost: 'Total Spend',
                hero_granted_quota: 'Total Quota',
                btn_manage_credits: 'Manage Credits',
                btn_refresh: 'Refresh',
                btn_view_all: 'All ➔',
                stat_today_calls: 'Today Calls',
                stat_today_tokens: 'Today Tokens',
                stat_total_calls: 'Total Calls',
                stat_total_tokens: 'Total Tokens',
                active_model_label: 'Active Model',
                btn_change_model: 'Change ➔',
                overview_activity_title: 'Activity & Token Ledger',
                overview_activity_desc: 'Live model invocation logs, token usage, and cost settlement',
                th_cost: 'Cost ($)',
                modal_credits_title: 'Manage Credits / Quota',
                modal_credits_desc: 'Set total credit allowance or top up ($ USD). Invocations deduct from this balance.',
                modal_credits_input: 'Credit Allowance ($ USD)',
                modal_credits_btn: 'Save Quota',
                btn_cancel: 'Cancel',
                timeline_title: 'Timeline',
                timeline_desc: 'Plugin interactions by channel; groups show group names, private chats show nicknames',
                persona_title: 'System Prompt Editor',
                persona_desc: 'Pure architecture: left empty by default for custom definition',
                btn_save_persona: 'Save & Apply',
                settings_model_title: 'Model Configuration',
                settings_active_model: 'Select Model',
                label_choose_preset: 'Preset Template',
                btn_sync_upstream: 'Sync Upstream Models',
                btn_save_settings: 'Save Settings',
                subtab_models: 'Models & Pricing',
                subtab_pool: 'API Key Pool',
                subtab_general: 'General',
                th_model_id: 'Model ID',
                th_model_name: 'Model Name',
                th_provider: 'Provider',
                th_input_price: 'Input $/MTok',
                th_output_price: 'Output $/MTok',
                th_cache_price: 'Cache $/MTok',
                api_pool_title: 'API Key Pool',
                btn_add_key: '+ Add Key',
                th_key_name: 'Name',
                th_key_provider: 'Provider',
                th_key_val: 'API Key',
                th_key_base: 'Base URL',
                th_key_status: 'Status',
                th_action: 'Action',
                settings_lang_title: 'General Settings',
                settings_lang_label: 'Language',
                settings_port_label: 'Listen Port',
                ext_title: 'Extensions',
                ext_desc: 'Applied only when external plugins are connected',
                btn_save_ext: 'Save Extensions',
                ext_daily_limit: 'Daily Limit',
                ext_session_limit: 'Session Limit',
                ext_interval: 'Interval (s)',
                ext_body_len: 'Comment Length',
                ext_master_uid: 'Target UID',
                ext_master_qq: 'Target QQ',
                ext_keywords: 'Keyword Pool',
                ext_target_pool: 'Tag Pool',
                toast_saved: 'Settings successfully saved',
                toast_prompt_saved: 'Prompt updated & applied',
                empty_timeline: 'No timeline events recorded',
                empty_snippet: 'No recent experience slices'
            }
        };

        function applyI18n() {
            const pack = i18n[currentLang] || i18n['zh-CN'];
            document.querySelectorAll('[data-i18n]').forEach(el => {
                const key = el.getAttribute('data-i18n');
                if (pack[key]) {
                    el.innerText = pack[key];
                }
            });
            document.title = pack.brand_title;
            const langSelect = document.getElementById('lang-select');
            if (langSelect) langSelect.value = currentLang;
            if (globalModelsConfig) {
                updateStripReasoningBadge(globalModelsConfig.reasoning_effort || 'none');
            }
        }

        function changeLanguage(lang) {
            currentLang = lang;
            localStorage.setItem('hub_lang', lang);
            applyI18n();
            showToast(currentLang === 'zh-CN' ? '已切换为简体中文' : 'Language set to English');
        }

        function showToast(msg) {
            const toast = document.getElementById('toast');
            if (!toast) return;
            toast.innerText = msg;
            toast.classList.add('show');
            clearTimeout(toast._timer);
            toast._timer = setTimeout(() => toast.classList.remove('show'), 2600);
        }

        function toggleSidebar(open) {
            const sidebar = document.querySelector('.sidebar');
            const overlay = document.getElementById('sidebar-overlay');
            if (!sidebar || !overlay) return;
            if (open === undefined) {
                open = !sidebar.classList.contains('open');
            }
            if (open) {
                sidebar.classList.add('open');
                overlay.classList.add('show');
            } else {
                sidebar.classList.remove('open');
                overlay.classList.remove('show');
            }
        }

        
        // ==========================================
        // 仪表盘数字大小渐进动效
        // ==========================================
        // ==========================================
        // 1. 通用平滑数值渐增滚动引擎 (Count-up Roll Engine)
        // ==========================================


        // 显式挂载到全局 window 对象，确保内联 onclick 永不丢失上下文
        window.toggleSidebar = toggleSidebar;
        window.navigateTab = navigateTab;
        window.loadData = loadData;
        // window.loadDetailedLedger mounted in ledger.js
        window.openSettingsTab = openSettingsTab;

        function navigateTab(tabId, el) {
            toggleSidebar(false);
            // 同步移动端底部快捷栏状态
            document.querySelectorAll('.nav-item').forEach(item => item.classList.remove('active'));
            const footerBtn = document.getElementById('footer-settings-btn');
            if (footerBtn) footerBtn.classList.remove('active');

            document.querySelectorAll('.view-section').forEach(sec => sec.classList.remove('active'));
            
            if (el) el.classList.add('active');
            const targetSection = document.getElementById(tabId);
            if (targetSection) targetSection.classList.add('active');

            if (tabId === 'tab-overview') {
                animateDashboardNumbers();
                fetchSystemResources();
                if (!resourceMonitorTimer) {
                    resourceMonitorTimer = setInterval(fetchSystemResources, 3500);
                }
            } else {
                if (resourceMonitorTimer) {
                    clearInterval(resourceMonitorTimer);
                    resourceMonitorTimer = null;
                }
            }
            if (tabId === 'tab-timeline') {
                if (typeof loadTimelineData === 'function') loadTimelineData();
            } else if (tabId === 'tab-ledger') {
                if (typeof loadDetailedLedger === 'function') loadDetailedLedger();
            } else if (tabId === 'tab-persona') {
                if (typeof ensurePersonaLoaded === 'function') ensurePersonaLoaded();
                if (typeof renderAffectionTiers === 'function') renderAffectionTiers();
                if (typeof renderSpeechRegexRules === 'function') renderSpeechRegexRules();
            } else if (tabId === 'tab-archives') {
                if (typeof loadArchives === 'function') loadArchives();
            } else if (tabId === 'tab-identity') {
                if (typeof loadIdentityScan === 'function') loadIdentityScan();
            } else if (tabId === 'tab-tools') {
                if (typeof loadToolsPage === 'function') loadToolsPage();
            }

            const pack = i18n[currentLang] || i18n['zh-CN'];
            const titleMap = {
                'tab-overview': pack.title_overview,
                'tab-timeline': pack.title_timeline,
                'tab-persona': pack.title_persona,
                'tab-archives': '档案',
                'tab-identity': '身份映射',
                'tab-tools': '工具',
                'tab-settings': pack.title_settings
            };
            const titleEl = document.getElementById('view-title');
            if (titleEl) titleEl.innerText = titleMap[tabId] || pack.brand_title;
        }

        function openSettingsTab(btn) {
            navigateTab('tab-settings', btn || document.getElementById('footer-settings-btn'));
        }

        function goToModelSettings() {
            openSettingsTab(document.getElementById('footer-settings-btn'));
            switchSettingsSubtab('subtab-model');
        }



        async function pollMetrics() {
            try {
                const res = await fetch('/api/overview');
                if (res.status === 401) {
                    window.location.href = '/login';
                    return;
                }
                const data = await res.json();
                window.lastDashboardData = data;
                const pack = i18n[currentLang] || i18n['zh-CN'];
                
                // 实时同步 AI 硬件算力占用看板
                if (data.resources && typeof renderAIResources === 'function') {
                    renderAIResources(data.resources);
                }

                // 顶部系统状态与时间
                const serverTimeEl = document.getElementById('server-time');
                if (serverTimeEl && data.summary) serverTimeEl.innerText = data.summary.server_time || '';
                const sysStatusEl = document.getElementById('sys-status');
                if (sysStatusEl) {
                    sysStatusEl.innerText = pack.status_online || 'Online';
                    sysStatusEl.style.color = 'var(--accent-green)';
                }

                // Token 与 账本统计
                const tkToday = (data.tokens && data.tokens.today) ? data.tokens.today : {};
                const credits = (data.tokens && data.tokens.credits) ? data.tokens.credits : {
                    balance_formatted: '$0.00',
                    today_cost_formatted: '$0.0000',
                    overall_cost_formatted: '$0.0000',
                    total_granted: 0.00
                };

                // 1. 核心余额看板（余额交给 updateCurrencyAndBalances 统一做数字滚动，避免这里直接覆盖）
                if (document.getElementById('hero-credits-balance')) {
                    const quotaSymbol = credits.currency === 'CNY' ? '¥' : '$';
                    document.getElementById('hero-granted-quota').innerText = quotaSymbol + Number(credits.total_granted || 0).toFixed(2);
                }

                // 2. 当前模型 / 今日调用 / 今日 Token / 今日费用
                if (document.getElementById('m-today-calls')) {
                    const animateReady = typeof animateDashboardNumbers === 'function';
                    const setText = (id, value) => {
                        const el = document.getElementById(id);
                        if (el) el.innerText = value;
                    };
                    setText('m-today-tokens-breakdown', `In: ${(tkToday.input_tokens || 0).toLocaleString()} · Out: ${(tkToday.output_tokens || 0).toLocaleString()} · Cache: ${(tkToday.cached_tokens || 0).toLocaleString()}`);
                    setText('m-today-cost-value', credits.today_cost_formatted || '$0.0000');
                    const activeMasterCred = ((globalModelsConfig && globalModelsConfig.master_credentials) || []).find(c => c.is_active !== false);
                    setText('m-today-cost-currency', (activeMasterCred && activeMasterCred.currency) || credits.currency || 'USD');
                    if (!window.__dashboardCounted && animateReady) {
                        // 首次交给 count-up 从 0 开始，避免先闪现最终值
                    } else {
                        setText('m-today-calls', (tkToday.calls || 0).toLocaleString());
                        setText('m-today-tokens-sum', (tkToday.total_tokens || 0).toLocaleString());
                    }
                    const actModel = (globalModelsConfig && globalModelsConfig.active_model) ? globalModelsConfig.active_model : '';
                    setText('m-active-model-tag', actModel || '/');
                }

                
                // 4. 渲染时间轴
                const timelineEvents = data.timeline || [];
                const fullContainer = document.getElementById('timeline-full-list');
                if (fullContainer) {
                    fullContainer.innerHTML = timelineEvents.map(renderTimelineCard).join('') || `<div style="color: var(--text-muted); font-size: 13px;">${pack.empty_timeline}</div>`;
                }

                if (!window.__dashboardCounted && typeof animateDashboardNumbers === 'function') {
                    window.__dashboardCounted = true;
                    animateDashboardNumbers();
                }

            } catch (err) {
                console.error(err);
                const sysStatusEl = document.getElementById('sys-status');
                if (sysStatusEl) {
                    sysStatusEl.innerText = 'Offline';
                    sysStatusEl.style.color = 'var(--accent-rose)';
                }
            }
        }

        // 初次加载或全量刷新数据
        async function loadInitialData() {
            try {
                const res = await fetch('/api/overview');
                if (res.status === 401) {
                    window.location.href = '/login';
                    return;
                }
                const data = await res.json();

                // 1. 立即渲染 AI 硬件算力占用看板 (GPU, CPU, RAM) - 进门首屏即刻呈现
                if (data.resources && typeof renderAIResources === 'function') {
                    renderAIResources(data.resources);
                }

                // 2. 立即渲染时间轴生命流 (记忆线) - 进门首屏即刻呈现
                if (data.timeline && typeof renderTimelineCard === 'function') {
                    const fullContainer = document.getElementById('timeline-full-list');
                    const pack = i18n[currentLang] || i18n['zh-CN'];
                    if (fullContainer && Array.isArray(data.timeline)) {
                        fullContainer.innerHTML = data.timeline.map(renderTimelineCard).join('') || `<div style="color: var(--text-muted); font-size: 13px;">${pack.empty_timeline}</div>`;
                    }
                }
                
                // 人格四段字段与自定义温差/正则规则载入
                if (data.current_persona && typeof applyPersonaProfile === 'function') {
                    applyPersonaProfile(data.current_persona);
                }
                if (data.persona_rules) {
                    window.personaRules = data.persona_rules;
                    if (typeof renderAffectionTiers === 'function') renderAffectionTiers();
                    if (typeof renderSpeechRegexRules === 'function') renderSpeechRegexRules();
                }

                // 存储全局配置并即刻同步顶部活动模型，杜绝斜杠 /
                if (data.models_config) {
                    globalModelsConfig = data.models_config;
                    const mList = globalModelsConfig.master_credentials || [];
                    const curMaster = mList.find(c => c.is_active && c.model) || mList[0];
                    const activeM = curMaster ? curMaster.model : (globalModelsConfig.active_model || 'gemini-3.8-flash');
                    const activeP = curMaster ? curMaster.provider : 'Google';
                    const activeR = curMaster ? curMaster.reasoning_effort : (globalModelsConfig.reasoning_effort || 'medium');
                    syncActiveModelToAllDisplays(activeM, activeP, activeR);
                }
                isInitialLoaded = true;

                // 动态无硬编码更新：根据当前实际访问的原点及激活模型更新 Base URL
                try {
                    const currentOrigin = window.location.origin;
                    const apiBaseEl = document.getElementById('uni-api-base');
                    if (apiBaseEl) {
                        apiBaseEl.value = currentOrigin + '/v1';
                    }
                    const apiModelEl = document.getElementById('uni-api-model');
                    if (apiModelEl && data.models_config && data.models_config.active_model) {
                        apiModelEl.value = data.models_config.active_model;
                    }
                    const portEl = document.getElementById('settings-port-value');
                    if (portEl) {
                        portEl.innerText = window.location.port
                            || (window.location.protocol === 'https:' ? '443' : '80');
                    }
                } catch (e) {}

                // 渲染模型配置 (确保模型和 Key 立即回填)
                // renderModelsConfigUI is replaced by renderCredentialsPoolUI
                renderCredentialsPoolUI();
                if (typeof updateCurrencyAndBalances === 'function') updateCurrencyAndBalances();

                // 统计由 startHubApp 统一并发触发，避免这里二次 pollMetrics 覆盖动画
                
                // 支持 URL Hash 极速直达设置页（模块详情页已下线，不再引用旧跳转）
                if (window.location.hash === '#settings') {
                    setTimeout(() => openSettingsTab(), 150);
                }
            } catch (err) {
                console.error('Failed to load initial data:', err);
            }
        }

        async function loadData() {
            await pollMetrics();
            showToast(currentLang === 'zh-CN' ? '数据已刷新' : 'Data refreshed');
        }

        
                


        async function handleLogout() {
            if (!confirm('确定要退出当前中枢控制台登录吗？')) return;
            try {
                await fetch('/api/auth/logout', { method: 'POST' });
            } catch (e) {}
            window.location.href = '/login';
        }

        async function submitChangePassword(btn) {
            const oldP = document.getElementById('pwd-old').value;
            const newP = document.getElementById('pwd-new').value;
            if (!oldP || !newP) {
                alert('请完整填写原密码与新密码');
                return;
            }
            if (newP.length < 12) {
                alert('新密码长度不能少于 12 位');
                return;
            }
            const orig = btn.innerText;
            btn.disabled = true;
            btn.innerText = '正在保存...';
            try {
                const res = await fetch('/api/auth/change_password', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ old_password: oldP, new_password: newP })
                });
                const data = await res.json();
                if (res.ok && data.success) {
                    showToast('✓ 密码修改成功，请重新登录');
                    setTimeout(() => { window.location.href = '/login'; }, 800);
                } else {
                    alert(data.error || data.message || '密码修改失败');
                }
            } catch (err) {
                alert('网络异常: ' + err.message);
            } finally {
                btn.disabled = false;
                btn.innerText = orig;
            }
        }




