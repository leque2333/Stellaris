// -*- coding: utf-8 -*-
// Stellaris - Models, Credentials & Compute Matrix Module

        function syncActiveModelToAllDisplays(modelName, provider, reasoning) {
            if (!modelName || modelName === '/') {
                if (globalModelsConfig && globalModelsConfig.active_model) {
                    modelName = globalModelsConfig.active_model;
                } else {
                    modelName = 'gemini-3.8-flash';
                }
            }
            // 1. 顶部右上角
            const topModelEl = document.getElementById('top-active-model');
            if (topModelEl) {
                topModelEl.innerText = modelName;
            }
            // 2. 看板当前模型
            const actModelEl = document.getElementById('m-active-model-tag');
            if (actModelEl) {
                actModelEl.innerText = modelName;
            }
            // 3. 顶部模型条
            const stripNameEl = document.getElementById('strip-model-name');
            if (stripNameEl) {
                stripNameEl.innerText = modelName;
                const badgeEl = document.getElementById('strip-provider-badge');
                if (badgeEl) {
                    badgeEl.innerText = provider || 'AI';
                    badgeEl.style.display = 'inline-block';
                }
            }
            if (reasoning && typeof updateStripReasoningBadge === 'function') {
                updateStripReasoningBadge(reasoning);
            }
        }

        function switchSettingsSubtab(subtabId, btn) {
            document.querySelectorAll('#tab-settings .subtab-content').forEach(c => c.classList.remove('active'));
            document.querySelectorAll('#tab-settings .subtab-btn').forEach(b => b.classList.remove('active'));
            const target = document.getElementById(subtabId);
            if (target) target.classList.add('active');
            if (btn) btn.classList.add('active');
            if (subtabId === 'subtab-memory-tuning' && typeof loadMemoryTuning === 'function') {
                loadMemoryTuning();
            }
            if (subtabId === 'subtab-general' && typeof loadPluginScanConfig === 'function') {
                loadPluginScanConfig();
            }
        }

        function switchPersonaSubtab(subtabId, btn) {
            document.querySelectorAll('#tab-persona .subtab-content').forEach(c => c.classList.remove('active'));
            document.querySelectorAll('#tab-persona .subtab-btn').forEach(b => b.classList.remove('active'));
            const target = document.getElementById(subtabId);
            if (target) target.classList.add('active');
            if (btn) btn.classList.add('active');
        }

        function getReasoningLabel(effort) {
            const isZh = currentLang === 'zh-CN';
            switch (effort) {
                case 'low': return isZh ? '低' : 'Low';
                case 'medium': return isZh ? '中' : 'Medium';
                case 'high': return isZh ? '高' : 'High';
                case 'native': return isZh ? '原生推理' : 'Native';
                default: return isZh ? '无' : 'None';
            }
        }

        function updateStripReasoningBadge(effort) {
            const stripVal = document.getElementById('strip-reasoning-val');
            if (stripVal) {
                stripVal.innerText = getReasoningLabel(effort);
                stripVal.style.color = (effort && effort !== 'none') ? 'var(--accent-cyan, #38bdf8)' : 'var(--text-muted)';
            }
        }

        // ==========================================
        // 凭证池与模型设置
        // ==========================================

        function renderCredentialsPoolUI() {
            if (!globalModelsConfig) return;
            globalModelsConfig.master_credentials = globalModelsConfig.master_credentials || [];
            globalModelsConfig.function_credentials = globalModelsConfig.function_credentials || [];

            renderSingleCredentialPool('master', globalModelsConfig.master_credentials);
            renderSingleCredentialPool('function', globalModelsConfig.function_credentials);

            // 更新顶部栏活动模型显示
            updateTopActiveModelDisplay();
        }

        
        // ==========================================
        // 核心额度看板：厂商主题色动态驱动系统
        // ==========================================


        function renderSingleCredentialPool(poolType, list) {
            const containerId = poolType === 'master' ? 'master-credentials-list' : 'function-credentials-list';
            const container = document.getElementById(containerId);
            if (!container) return;

            if (!list || list.length === 0) {
                container.innerHTML = `
                    <div style="background:#ffffff;border-radius:12px;padding:26px 20px;text-align:center;color:#94a3b8;font-size:13px;width:100%;box-sizing:border-box;">
                        <div>暂无${poolType === 'master' ? '主控' : '功能'}凭证，点击右上角「+ 添加」创建一条新凭证</div>
                    </div>`;
                return;
            }

            const ordered = list.map((cred, idx) => ({ cred, idx })).sort((a, b) => {
                const aa = a.cred.is_active !== false;
                const bb = b.cred.is_active !== false;
                if (aa !== bb) return aa ? -1 : 1;
                return a.idx - b.idx;
            });

            const html = ordered.map(({ cred, idx }) => {
                const name = cred.name || `凭证 #${idx + 1}`;
                const provider = cred.provider || (cred.auth_type === 'ollama' ? 'Ollama' : (cred.auth_type === 'ide_bridge' ? 'IDE Bridge' : '未知服务商'));
                const model = cred.model || '未绑定模型';
                const active = cred.is_active !== false;
                const inP = cred.input_price;
                const outP = cred.output_price;
                const rowCurrency = cred.currency || (typeof detectCredentialCurrency === 'function' ? detectCredentialCurrency(cred) : 'USD');
                const priceSymbol = String(rowCurrency).toUpperCase() === 'CNY' ? '¥' : '$';
                const scheduleCount = Array.isArray(cred.price_schedule) ? cred.price_schedule.length : 0;
                const priceText = (inP === undefined || inP === null || inP === '' || outP === undefined || outP === null || outP === '')
                    ? '未定价'
                    : `入 ${priceSymbol}${inP} / 出 ${priceSymbol}${outP}` + (scheduleCount ? ` · 分时 ${scheduleCount} 段` : '');
                return `
                    <div class="credential-row ${active ? '' : 'disabled'}" onclick="openCredentialEditor('${poolType}', ${idx})">
                        <div class="credential-row-main">
                            <div class="credential-row-name">${escapeHtml(name)}</div>
                            <div class="credential-row-meta">${escapeHtml(provider)} · ${escapeHtml(model)} · ${escapeHtml(cred.reasoning_effort || 'none')}</div>
                        </div>
                        <div class="credential-row-price">${escapeHtml(priceText)}</div>
                    </div>`;
            }).join('');

            container.innerHTML = `<div class="credential-list">${html}</div>`;
        }

        let credentialEditor = null;

        function buildCredentialModelOptions(current) {
            const presets = globalModelsConfig.preset_templates || [];
            const providers = ['Google', 'DeepSeek', 'Anthropic', 'OpenAI', 'OpenRouter', 'Ollama', 'IDE Bridge'];
            let html = '<option value="">-- 请选择模型 --</option>';
            let found = false;
            providers.forEach(p => {
                const sub = presets.filter(item => (item.provider || '').toLowerCase() === p.toLowerCase());
                if (!sub.length) return;
                html += `<optgroup label="${p}">`;
                sub.forEach(item => {
                    const sel = item.id === current ? 'selected' : '';
                    if (sel) found = true;
                    html += `<option value="${item.id}" ${sel}>${escapeHtml(item.name || item.id)}</option>`;
                });
                html += '</optgroup>';
            });
            if (current && !found) {
                html = `<option value="${escapeHtml(current)}" selected>${escapeHtml(current)}</option>` + html;
            }
            return html;
        }

        function setCredentialEditorReadonly(readonly) {
            const modal = document.getElementById('credential-edit-modal');
            if (!modal) return;
            modal.querySelectorAll('.form-input, .form-select').forEach((el) => {
                el.disabled = Boolean(readonly);
            });
            const hint = document.getElementById('cred-edit-readonly-hint');
            if (hint) hint.style.display = readonly ? 'block' : 'none';
        }

        function renderCredentialEditorActions(active) {
            const box = document.getElementById('cred-edit-header-actions');
            if (!box) return;
            box.innerHTML = active
                ? `<button class="btn" onclick="saveCredentialEditor()">保存</button>
                   <button class="btn btn-secondary" onclick="disableCredentialFromEditor()">停用</button>`
                : `<button class="btn" onclick="enableCredentialFromEditor()">启用</button>
                   <button class="btn btn-secondary" style="color:#dc2626;" onclick="deleteCredentialFromEditor()">删除</button>`;
        }

        function priceWeekdayOptions(selected) {
            const names = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];
            return names.map((name, index) => {
                const value = index + 1;
                return `<option value="${value}"${value === Number(selected) ? ' selected' : ''}>${name}</option>`;
            }).join('');
        }

        function priceScheduleRow(item = {}) {
            const value = (key, fallback = '') => escapeHtml(item[key] ?? fallback);
            const weekdays = String(item.weekdays || '1-7');
            const [weekStart, weekEnd] = weekdays.includes('-')
                ? weekdays.split('-')
                : [weekdays, weekdays];
            return `
                <div class="cred-price-schedule-row">
                    <div class="cred-schedule-line">
                        <label class="cred-schedule-field cred-schedule-week">
                            <span>周</span>
                            <select class="form-select cred-schedule-week-start" title="起始星期">${priceWeekdayOptions(weekStart)}</select>
                            <span class="cred-schedule-sep">→</span>
                            <select class="form-select cred-schedule-week-end" title="结束星期">${priceWeekdayOptions(weekEnd)}</select>
                        </label>
                        <label class="cred-schedule-field cred-schedule-time">
                            <span>时间</span>
                            <input class="form-input cred-schedule-start" type="time" value="${value('start')}" title="开始时间">
                            <span class="cred-schedule-sep">→</span>
                            <input class="form-input cred-schedule-end" type="time" value="${value('end')}" title="结束时间">
                        </label>
                        <button class="btn btn-subtle cred-schedule-remove" type="button" onclick="removePriceScheduleRow(this)" title="删除时段">✕</button>
                    </div>
                    <div class="cred-schedule-prices">
                        <label>输入 / 1M <input class="form-input cred-schedule-input" type="number" step="0.001" placeholder="输入价" value="${value('input_price')}"></label>
                        <label>缓存 / 1M <input class="form-input cred-schedule-cache" type="number" step="0.001" placeholder="缓存价" value="${value('cache_price')}"></label>
                        <label>输出 / 1M <input class="form-input cred-schedule-output" type="number" step="0.001" placeholder="输出价" value="${value('output_price')}"></label>
                    </div>
                </div>`;
        }

        function renderPriceSchedule(schedule) {
            const host = document.getElementById('cred-price-schedule');
            if (!host) return;
            const list = Array.isArray(schedule) ? schedule : [];
            host.innerHTML = list.map((item) => priceScheduleRow(item)).join('');
        }

        function addPriceScheduleRow() {
            const host = document.getElementById('cred-price-schedule');
            if (!host) return;
            const base = {
                start: '08:00',
                end: '22:00',
                input_price: document.getElementById('cred-edit-input-price')?.value || '',
                cache_price: document.getElementById('cred-edit-cache-price')?.value || '',
                output_price: document.getElementById('cred-edit-output-price')?.value || '',
            };
            host.insertAdjacentHTML('beforeend', priceScheduleRow(base));
        }

        function removePriceScheduleRow(button) {
            button?.closest('.cred-price-schedule-row')?.remove();
        }

        function readPriceSchedule() {
            return Array.from(document.querySelectorAll('#cred-price-schedule .cred-price-schedule-row')).map((row) => {
                const weekStart = row.querySelector('.cred-schedule-week-start')?.value || '1';
                const weekEnd = row.querySelector('.cred-schedule-week-end')?.value || '7';
                return {
                    weekdays: `${weekStart}-${weekEnd}`,
                    start: row.querySelector('.cred-schedule-start')?.value || '',
                    end: row.querySelector('.cred-schedule-end')?.value || '',
                    input_price: parseFloat(row.querySelector('.cred-schedule-input')?.value || '0') || 0,
                    cache_price: parseFloat(row.querySelector('.cred-schedule-cache')?.value || '0') || 0,
                    output_price: parseFloat(row.querySelector('.cred-schedule-output')?.value || '0') || 0,
                };
            }).filter((item) => item.start && item.end);
        }

        function openCredentialEditor(poolType, idx) {
            if (!globalModelsConfig) return;
            const list = poolType === 'master' ? globalModelsConfig.master_credentials : globalModelsConfig.function_credentials;
            const cred = list && list[idx];
            if (!cred) return;
            credentialEditor = { poolType, idx };
            const set = (id, value) => { const el = document.getElementById(id); if (el) el.value = value ?? ''; };
            const title = document.getElementById('cred-edit-title');
            if (title) title.innerText = `${poolType === 'master' ? '主控' : '功能'}凭证`;
            set('cred-edit-name', cred.name || '');
            set('cred-edit-provider', cred.provider || '');
            const modelEl = document.getElementById('cred-edit-model');
            if (modelEl) modelEl.innerHTML = buildCredentialModelOptions(cred.model || '');
            set('cred-edit-key', cred.auth_type === 'ollama' || cred.auth_type === 'ide_bridge' ? (cred.base_url || '') : (cred.api_key || ''));
            set('cred-edit-base', cred.base_url || '');
            set('cred-edit-reasoning', cred.reasoning_effort || 'none');
            set('cred-edit-input-price', cred.input_price ?? '');
            set('cred-edit-cache-price', cred.cache_price ?? '');
            set('cred-edit-output-price', cred.output_price ?? '');
            set('cred-edit-currency', cred.currency || detectCredentialCurrency(cred));
            renderPriceSchedule(cred.price_schedule || []);
            const authWrap = document.getElementById('cred-edit-authtype-wrap');
            if (authWrap) authWrap.style.display = poolType === 'function' ? '' : 'none';
            const auth = document.getElementById('cred-edit-authtype');
            if (auth) auth.value = cred.auth_type || 'official_api';
            updateCredentialEditorKeyLabel();
            renderCredentialEditorActions(cred.is_active !== false);
            setCredentialEditorReadonly(cred.is_active === false);
            const modal = document.getElementById('credential-edit-modal');
            if (modal) modal.classList.add('show');
        }

        function closeCredentialEditor() {
            const modal = document.getElementById('credential-edit-modal');
            if (modal) modal.classList.remove('show');
            credentialEditor = null;
        }

        function updateCredentialEditorKeyLabel() {
            const auth = document.getElementById('cred-edit-authtype')?.value || 'official_api';
            const label = document.getElementById('cred-edit-key-label');
            const key = document.getElementById('cred-edit-key');
            if (!label || !key) return;
            if (auth === 'ollama' || auth === 'ide_bridge') {
                label.innerText = '服务地址';
                key.type = 'text';
                key.placeholder = 'http://127.0.0.1:11434/v1';
            } else {
                label.innerText = 'API 密钥';
                key.type = 'password';
                key.placeholder = '输入密钥';
            }
        }

        function onCredentialEditorAuthTypeChange(authType) {
            updateCredentialEditorKeyLabel();
            if (!globalModelsConfig || !credentialEditor) return;
            const { poolType, idx } = credentialEditor;
            if (poolType !== 'function') return;
            const cred = (globalModelsConfig.function_credentials || [])[idx];
            if (!cred) return;
            if (authType === 'ollama') {
                document.getElementById('cred-edit-provider').value = 'Ollama';
                document.getElementById('cred-edit-base').value = 'http://127.0.0.1:11434/v1';
                document.getElementById('cred-edit-key').value = 'http://127.0.0.1:11434/v1';
            } else if (authType === 'ide_bridge') {
                document.getElementById('cred-edit-provider').value = 'IDE Bridge';
                document.getElementById('cred-edit-base').value = 'http://127.0.0.1:18890/v1';
                document.getElementById('cred-edit-key').value = 'http://127.0.0.1:18890/v1';
            }
        }

        function onCredentialEditorModelChange(modelId) {
            if (!globalModelsConfig) return;
            const presets = globalModelsConfig.preset_templates || [];
            const matched = presets.find(item => item.id === modelId)
                || (globalModelsConfig.models || []).find(item => item.id === modelId);
            if (!matched) return;
            const provider = document.getElementById('cred-edit-provider');
            if (provider && matched.provider) provider.value = matched.provider;
            // 价格不再自动填写，由用户手动输入
        }

        function applyCredentialEditorFields() {
            if (!globalModelsConfig || !credentialEditor) return null;
            const { poolType, idx } = credentialEditor;
            const list = poolType === 'master' ? globalModelsConfig.master_credentials : globalModelsConfig.function_credentials;
            const cred = list && list[idx];
            if (!cred) return null;
            const val = (id) => document.getElementById(id)?.value ?? '';
            const num = (id) => parseFloat(document.getElementById(id)?.value || '0') || 0;
            const authType = document.getElementById('cred-edit-authtype')?.value || 'official_api';
            const isLocal = poolType === 'function' && (authType === 'ollama' || authType === 'ide_bridge');
            cred.name = val('cred-edit-name').trim() || cred.name;
            cred.provider = val('cred-edit-provider').trim() || cred.provider;
            cred.auth_type = authType;
            cred.model = val('cred-edit-model');
            cred.base_url = val('cred-edit-base').trim();
            if (isLocal) {
                if (val('cred-edit-key').trim()) cred.base_url = val('cred-edit-key').trim();
                cred.api_key = authType === 'ollama' ? 'ollama-local' : 'local-ide-session';
            } else {
                cred.api_key = val('cred-edit-key').trim();
            }
            cred.reasoning_effort = val('cred-edit-reasoning') || 'none';
            cred.input_price = num('cred-edit-input-price');
            cred.cache_price = num('cred-edit-cache-price');
            cred.output_price = num('cred-edit-output-price');
            cred.currency = val('cred-edit-currency') || 'USD';
            cred.price_schedule = readPriceSchedule();
            return cred;
        }

        function syncMasterModelFromFirstActive() {
            const list = globalModelsConfig.master_credentials || [];
            const active = list.find(item => item.is_active !== false && item.model);
            if (!active) return;
            globalModelsConfig.active_model = active.model;
            globalModelsConfig.reasoning_effort = active.reasoning_effort || 'none';
            if (typeof syncActiveModelToAllDisplays === 'function') {
                syncActiveModelToAllDisplays(active.model, active.provider, active.reasoning_effort);
            }
        }

        function saveCredentialEditor() {
            const cred = applyCredentialEditorFields();
            if (!cred) return;
            if (credentialEditor.poolType === 'master') syncMasterModelFromFirstActive();
            closeCredentialEditor();
            renderCredentialsPoolUI();
            saveCredentialsPoolConfig(true);
        }

        function disableCredentialFromEditor() {
            if (!credentialEditor) return;
            const { poolType, idx } = credentialEditor;
            const list = poolType === 'master' ? globalModelsConfig.master_credentials : globalModelsConfig.function_credentials;
            const cred = list && list[idx];
            if (!cred) return;
            const activeCount = (list || []).filter(item => item.is_active !== false).length;
            if (activeCount <= 1) {
                if (typeof showToast === 'function') showToast('至少保留一个启用凭证');
                return;
            }
            cred.is_active = false;
            closeCredentialEditor();
            renderCredentialsPoolUI();
            saveCredentialsPoolConfig(true);
        }

        function enableCredentialFromEditor() {
            if (!credentialEditor) return;
            const { poolType, idx } = credentialEditor;
            const list = poolType === 'master' ? globalModelsConfig.master_credentials : globalModelsConfig.function_credentials;
            const cred = list && list[idx];
            if (!cred) return;
            cred.is_active = true;
            if (poolType === 'master') syncMasterModelFromFirstActive();
            saveCredentialsPoolConfig(true);
            renderCredentialEditorActions(true);
            setCredentialEditorReadonly(false);
            renderCredentialsPoolUI();
        }

        function deleteCredentialFromEditor() {
            if (!credentialEditor) return;
            const { poolType, idx } = credentialEditor;
            const list = poolType === 'master' ? globalModelsConfig.master_credentials : globalModelsConfig.function_credentials;
            const cred = list && list[idx];
            if (!cred) return;
            if (cred.is_active !== false) {
                if (typeof showToast === 'function') showToast('先停用再删除');
                return;
            }
            if (!window.confirm('删除这条凭证？')) return;
            list.splice(idx, 1);
            closeCredentialEditor();
            renderCredentialsPoolUI();
            saveCredentialsPoolConfig(true);
        }

        window.openCredentialEditor = openCredentialEditor;
        window.closeCredentialEditor = closeCredentialEditor;
        window.onCredentialEditorAuthTypeChange = onCredentialEditorAuthTypeChange;
        window.onCredentialEditorModelChange = onCredentialEditorModelChange;
        window.saveCredentialEditor = saveCredentialEditor;
        window.disableCredentialFromEditor = disableCredentialFromEditor;
        window.enableCredentialFromEditor = enableCredentialFromEditor;
        window.deleteCredentialFromEditor = deleteCredentialFromEditor;

        function addCredentialRow(poolType) {
            if (!globalModelsConfig) return;
            const targetList = poolType === 'master' ? 'master_credentials' : 'function_credentials';
            globalModelsConfig[targetList] = globalModelsConfig[targetList] || [];

            const newCred = {
                id: 'cred_' + Date.now(),
                name: (poolType === 'master' ? '主控凭证 #' : '功能凭证 #') + (globalModelsConfig[targetList].length + 1),
                provider: poolType === 'master' ? 'Google' : 'DeepSeek',
                auth_type: 'official_api',
                api_key: '',
                base_url: '',
                model: poolType === 'master' ? 'gemini-3.8-flash' : 'deepseek-chat',
                reasoning_effort: poolType === 'master' ? 'medium' : 'none',
                is_active: true
            };

            globalModelsConfig[targetList].push(newCred);
            renderCredentialsPoolUI();
            openCredentialEditor(poolType, globalModelsConfig[targetList].length - 1);
        }

        async function saveCredentialsPoolConfig(silent = false) {
            if (!globalModelsConfig) return;
            
            // 确保主控有效激活模型同步到 active_model
            const masterList = globalModelsConfig.master_credentials || [];
            const activeMaster = masterList.find(c => c.is_active && c.model) || masterList[0];
            if (activeMaster && activeMaster.model) {
                globalModelsConfig.active_model = activeMaster.model;
                if (activeMaster.reasoning_effort) {
                    globalModelsConfig.reasoning_effort = activeMaster.reasoning_effort;
                }
            }

            try {
                const res = await fetch('/api/models_config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(globalModelsConfig)
                });
                const ret = await res.json();
                if (ret.success) {
                    if (!silent) showToast(ret.message || '凭证池配置已成功保存并实时同步');
                    updateTopActiveModelDisplay();
                    if (typeof updateCurrencyAndBalances === 'function') updateCurrencyAndBalances();
                } else {
                    if (!silent) showToast('保存失败: ' + (ret.error || '未知错误'));
                }
            } catch (e) {
                if (!silent) showToast('网络请求异常，保存未完成');
            }
        }

        async function syncUpstreamModels() {
            showToast('正在从在线源拉取最新模型...');
            try {
                const res = await fetch('/api/models/sync_upstream');
                const data = await res.json();
                if (data.success) {
                    showToast('已同步 ' + data.count + ' 个模型');
                    await loadInitialData();
                } else {
                    showToast('使用内置预设矩阵');
                }
            } catch (e) {
                showToast('网络请求异常');
            }
        }



// Explicit Window Exports for Global Invocations
window.switchSettingsSubtab = switchSettingsSubtab;
window.switchPersonaSubtab = switchPersonaSubtab;
window.syncUpstreamModels = syncUpstreamModels;
window.saveCredentialsPoolConfig = saveCredentialsPoolConfig;
window.addCredentialRow = addCredentialRow;
window.renderCredentialsPoolUI = renderCredentialsPoolUI;
window.renderSingleCredentialPool = renderSingleCredentialPool;
window.changeLanguage = changeLanguage;
window.submitChangePassword = submitChangePassword;
window.syncActiveModelToAllDisplays = syncActiveModelToAllDisplays;

