// -*- coding: utf-8 -*-
// Stellaris - Models, Credentials & Compute Matrix Module

        function openCreditsModal() {
            let currBal = 0.0;
            if (globalModelsConfig && globalModelsConfig.credits) {
                currBal = globalModelsConfig.credits.balance !== undefined ? globalModelsConfig.credits.balance : 0.0;
            }
            const balEl = document.getElementById('modal-curr-balance');
            if (balEl) {
                const sym = (globalModelsConfig?.credits?.currency === 'CNY') ? '¥' : '$';
                const value = Number(currBal) || 0;
                balEl.innerText = `${value < 0 ? '-' : ''}${sym}${Math.abs(value).toFixed(2)}`;
            }
            const inp = document.getElementById('input-credits-topup');
            if (inp) inp.value = '10.00';
            const modal = document.getElementById('credits-modal');
            if (modal) modal.classList.add('show');
        }

        function closeCreditsModal() {
            const modal = document.getElementById('credits-modal');
            if (modal) modal.classList.remove('show');
        }

        function setQuickTopUp(val) {
            const inp = document.getElementById('input-credits-topup');
            if (!inp) return;
            inp.value = parseFloat(val).toFixed(2);
        }

        async function submitCreditsTopUp() {
            const inp = document.getElementById('input-credits-topup');
            if (!inp) return;
            const topUpVal = parseFloat(inp.value);
            if (isNaN(topUpVal) || topUpVal <= 0) {
                showToast('请输入有效充值金额 (大于0)', 'error');
                return;
            }
            let currBal = 0.0;
            let currTotal = 0.0;
            if (globalModelsConfig && globalModelsConfig.credits) {
                currBal = parseFloat(globalModelsConfig.credits.balance || 0.0);
                currTotal = parseFloat(globalModelsConfig.credits.total_quota || 0.0);
            }
            const newBal = currBal + topUpVal;
            const newTotal = currTotal + topUpVal;
            try {
                const res = await fetch('/api/credits', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        top_up_amount: topUpVal,
                        balance: newBal,
                        total_quota: newTotal
                    })
                });
                const data = await res.json();
                if (data.success) {
                    if (globalModelsConfig) {
                        globalModelsConfig.credits = data.credits;
                    }
                    showToast(`成功充值 $${topUpVal.toFixed(2)}，已实时入账！`);
                    closeCreditsModal();
                    await pollMetrics();
                } else {
                    showToast('充值失败: ' + (data.error || '未知错误'), 'error');
                }
            } catch (e) {
                showToast('网络请求异常: ' + e, 'error');
            }
        }

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

        function toggleKeyVisibility(inputId) {
            const el = document.getElementById(inputId);
            if (!el) return;
            el.type = (el.type === 'password') ? 'text' : 'password';
        }



        function switchSettingsSubtab(subtabId, btn) {
            document.querySelectorAll('#tab-settings .subtab-content').forEach(c => c.classList.remove('active'));
            document.querySelectorAll('#tab-settings .subtab-btn').forEach(b => b.classList.remove('active'));
            const target = document.getElementById(subtabId);
            if (target) target.classList.add('active');
            if (btn) btn.classList.add('active');
        }

        function switchPersonaSubtab(subtabId, btn) {
            document.querySelectorAll('#tab-persona .subtab-content').forEach(c => c.classList.remove('active'));
            document.querySelectorAll('#tab-persona .subtab-btn').forEach(b => b.classList.remove('active'));
            const target = document.getElementById(subtabId);
            if (target) target.classList.add('active');
            if (btn) btn.classList.add('active');
        }

                                        function getModelReasoningOptions(modelId) {
            if (!modelId) return ['none'];
            const cleanId = modelId.split('/').pop().toLowerCase().trim();
            const presets = (globalModelsConfig && globalModelsConfig.preset_templates) || [];
            const models = (globalModelsConfig && globalModelsConfig.models) || [];
            const curObj = models.find(m => m.id.toLowerCase() === cleanId || (m.full_id && m.full_id.toLowerCase().endsWith('/' + cleanId))) ||
                           presets.find(m => m.id.toLowerCase() === cleanId || (m.full_id && m.full_id.toLowerCase().endsWith('/' + cleanId)));
            if (curObj && curObj.available_reasoning_efforts && curObj.available_reasoning_efforts.length > 0) {
                return curObj.available_reasoning_efforts;
            }
            return ['none'];
        }

        function updateReasoningDropdownOptions(modelId) {
            const sel = document.getElementById('form-model-reasoning');
            if (!sel) return;
            const available = getModelReasoningOptions(modelId);
            const isZh = currentLang === 'zh-CN';
            const labelMap = {
                'none': isZh ? '无' : 'None',
                'native': isZh ? '原生推理' : 'Native',
                'low': isZh ? '低' : 'Low',
                'medium': isZh ? '中' : 'Medium',
                'high': isZh ? '高' : 'High'
            };

            let curEffort = globalModelsConfig.reasoning_effort || 'medium';
            if (!available.includes(curEffort)) {
                curEffort = available[0];
                globalModelsConfig.reasoning_effort = curEffort;
            }

            sel.innerHTML = available.map(opt => `<option value="${opt}" ${opt === curEffort ? 'selected' : ''}>${labelMap[opt] || opt}</option>`).join('');
            sel.value = curEffort;
            const isUnsupported = (available.length === 1 && available[0] === 'none');
            sel.disabled = isUnsupported;
            updateStripReasoningBadge(curEffort);
        }

        function checkModelReasoningSupport(modelId) {
            const opts = getModelReasoningOptions(modelId);
            return opts.length > 1 || (opts.length === 1 && opts[0] !== 'none');
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
                const priceText = (inP === undefined || inP === null || inP === '' || outP === undefined || outP === null || outP === '')
                    ? '未定价'
                    : `入 ${priceSymbol}${inP} / 出 ${priceSymbol}${outP}`;
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

        function updateCredentialField(poolType, idx, field, val) {
            if (!globalModelsConfig) return;
            const targetList = poolType === 'master' ? 'master_credentials' : 'function_credentials';
            if (globalModelsConfig[targetList] && globalModelsConfig[targetList][idx]) {
                globalModelsConfig[targetList][idx][field] = val;
                // 若主控凭证的关键属性发生输入变更，实时触发看板变色联动
                if (poolType === 'master' && (field === 'provider' || field === 'model' || field === 'base_url' || field === 'name')) {
                    if (typeof updateTopActiveModelDisplay === 'function') {
                        updateTopActiveModelDisplay();
                    }
                }
            }
        }

        function onCredentialAuthTypeChange(poolType, idx, authType) {
            if (!globalModelsConfig) return;
            const cred = (globalModelsConfig.function_credentials || [])[idx];
            if (!cred) return;
            cred.auth_type = authType;

            if (authType === 'ollama') {
                cred.provider = 'Ollama';
                cred.base_url = 'http://127.0.0.1:11434/v1';
                cred.api_key = 'ollama-local';
                cred.model = 'llama3.3';
                cred.input_price = 0;
                cred.output_price = 0;
                cred.cache_price = 0;
            } else if (authType === 'ide_bridge') {
                cred.provider = 'IDE Bridge';
                cred.base_url = 'http://127.0.0.1:18890/v1';
                cred.api_key = 'local-ide-session';
                cred.model = 'gemini-3.8-flash';
                cred.input_price = 0;
                cred.output_price = 0;
                cred.cache_price = 0;
            } else {
                cred.provider = 'DeepSeek';
                cred.api_key = '';
                cred.base_url = 'https://api.deepseek.com/v1';
                cred.model = 'deepseek-chat';
            }
            renderCredentialsPoolUI();
        }

        async function onCredentialKeyInput(poolType, idx, keyVal) {
            if (!globalModelsConfig) return;
            const targetList = poolType === 'master' ? 'master_credentials' : 'function_credentials';
            const cred = globalModelsConfig[targetList] ? globalModelsConfig[targetList][idx] : null;
            if (!cred) return;

            const trimmedKey = (keyVal || '').trim();
            if (cred.auth_type === 'ollama' || cred.auth_type === 'ide_bridge') {
                cred.base_url = trimmedKey;
                return;
            }

            cred.api_key = trimmedKey;
            if (!trimmedKey) return;

            // 自动识别服务商与推荐模型
            let detectedProvider = 'Custom';
            let recommendedModel = cred.model;

            if (trimmedKey.startsWith('AIza') || trimmedKey.startsWith('AQ.') || trimmedKey.includes('generativelanguage')) {
                detectedProvider = 'Google';
                recommendedModel = 'gemini-3.8-flash';
            } else if (trimmedKey.startsWith('sk-ant-')) {
                detectedProvider = 'Anthropic';
                recommendedModel = 'claude-3-7-sonnet-20250219';
            } else if (trimmedKey.startsWith('sk-or-')) {
                detectedProvider = 'OpenRouter';
                recommendedModel = 'deepseek/deepseek-chat';
            } else if (trimmedKey.startsWith('sk-')) {
                if (trimmedKey.length === 35 || trimmedKey.toLowerCase().includes('deepseek')) {
                    detectedProvider = 'DeepSeek';
                    recommendedModel = 'deepseek-chat';
                } else {
                    detectedProvider = 'OpenAI';
                    recommendedModel = 'gpt-4o';
                }
            }

            cred.provider = detectedProvider;
            if (recommendedModel && (!cred.model || cred.model.includes('gemini') && detectedProvider !== 'Google')) {
                cred.model = recommendedModel;
            }

            // 自动从预设模板中匹配定价
            const presets = globalModelsConfig.preset_templates || [];
            const matchedPreset = presets.find(p => p.id === cred.model) || presets.find(p => (p.provider || '').toLowerCase() === detectedProvider.toLowerCase());
            if (matchedPreset) {
                if (!cred.model) cred.model = matchedPreset.id;
                cred.input_price = matchedPreset.input_price !== undefined ? matchedPreset.input_price : cred.input_price;
                cred.output_price = matchedPreset.output_price !== undefined ? matchedPreset.output_price : cred.output_price;
                cred.cache_price = matchedPreset.cache_price !== undefined ? matchedPreset.cache_price : cred.cache_price;
            }

            renderCredentialsPoolUI();
        }

        function onCredentialModelChange(poolType, idx, modelId) {
            if (!globalModelsConfig) return;
            const targetList = poolType === 'master' ? 'master_credentials' : 'function_credentials';
            const cred = globalModelsConfig[targetList] ? globalModelsConfig[targetList][idx] : null;
            if (!cred) return;

            cred.model = modelId;
            const presets = globalModelsConfig.preset_templates || [];
            const matched = presets.find(p => p.id === modelId);
            if (matched) {
                cred.provider = matched.provider || cred.provider;
                cred.input_price = matched.input_price !== undefined ? matched.input_price : 0;
                cred.output_price = matched.output_price !== undefined ? matched.output_price : 0;
                cred.cache_price = matched.cache_price !== undefined ? matched.cache_price : 0;
            }
            // 实时联动：若修改的是生效主控，立即同步全局顶部显示并静默保存
            if (poolType === 'master' && cred.is_active) {
                globalModelsConfig.active_model = modelId;
                syncActiveModelToAllDisplays(modelId, cred.provider, cred.reasoning_effort);
                saveCredentialsPoolConfig(true);
            }
            renderCredentialsPoolUI();
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

        async function saveModelsConfig(silent = false) {
            if (!globalModelsConfig) return;
            const id = (document.getElementById('form-model-id').value || '').trim();
            if (id) {
                globalModelsConfig.active_model = id;
                const name = (document.getElementById('form-model-name').value || id).trim();
                const provider = (document.getElementById('form-model-provider').value || 'Custom').trim();
                const inPrice = parseFloat(document.getElementById('form-model-inprice').value) || 0;
                const outPrice = parseFloat(document.getElementById('form-model-outprice').value) || 0;
                const cachePrice = parseFloat(document.getElementById('form-model-cacheprice').value) || 0;

                globalModelsConfig.models = globalModelsConfig.models || [];
                const existIdx = globalModelsConfig.models.findIndex(m => m.id === id);
                const curItem = {
                    id: id,
                    name: name,
                    provider: provider,
                    input_price: inPrice,
                    output_price: outPrice,
                    cache_price: cachePrice,
                    currency: 'USD'
                };
                if (existIdx >= 0) {
                    globalModelsConfig.models[existIdx] = curItem;
                } else {
                    globalModelsConfig.models.unshift(curItem);
                }
            }

            const effortSel = document.getElementById('form-model-reasoning');
            if (effortSel) {
                globalModelsConfig.reasoning_effort = effortSel.value || 'none';
            }

            const pack = i18n[currentLang] || i18n['zh-CN'];
            try {
                const res = await fetch('/api/models_config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(globalModelsConfig)
                });
                const ret = await res.json();
                if (ret.success) {
                    if (ret.downgraded_reasoning) {
                        globalModelsConfig.reasoning_effort = 'none';
                        if (effortSel) effortSel.value = 'none';
                        updateStripReasoningBadge('none');
                        showToast(currentLang === 'zh-CN' ? '当前模型不支持调控思考强度，已自动重置为无' : 'Current model does not support reasoning effort; reset to None');
                    } else if (!silent) {
                        showToast(pack.toast_saved);
                    }
                    return ret;
                } else {
                    if (!silent) showToast('保存失败');
                }
            } catch (e) {
                if (!silent) showToast('网络请求异常');
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



        async function loadComputeMatrix() {
            try {
                const res = await fetch('/api/compute/config');
                if (!res.ok) return;
                globalComputeConfig = await res.json();
                renderComputeMatrix();
            } catch (e) {
                console.error("加载算力矩阵失败:", e);
            }
        }

        function renderComputeMatrix() {
            if (!globalComputeConfig) return;
            const slots = globalComputeConfig.active_slots || {};
            const providers = globalComputeConfig.providers || {};

            // 1. 渲染槽位下拉选择
            const pSelect = document.getElementById('slot-persona-provider');
            const lSelect = document.getElementById('slot-logic-provider');
            if (pSelect && lSelect) {
                pSelect.innerHTML = '';
                lSelect.innerHTML = '';
                Object.keys(providers).forEach(k => {
                    const p = providers[k];
                    const opt1 = document.createElement('option');
                    opt1.value = k;
                    opt1.textContent = `${p.name} (${p.model || 'default'})`;
                    if (slots.persona_slot === k) opt1.selected = true;
                    pSelect.appendChild(opt1);

                    const opt2 = document.createElement('option');
                    opt2.value = k;
                    opt2.textContent = `${p.name} (${p.model || 'default'})`;
                    if (slots.logic_slot === k) opt2.selected = true;
                    lSelect.appendChild(opt2);
                });
            }

            // 2. 渲染各 Provider 卡片
            const listEl = document.getElementById('compute-providers-list');
            if (!listEl) return;
            listEl.innerHTML = '';

            Object.keys(providers).forEach(pKey => {
                const p = providers[pKey];
                const card = document.createElement('div');
                card.style.cssText = 'background: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.08); border-radius: var(--radius-sm); padding: 14px 16px; display: flex; flex-direction: column; gap: 10px;';
                
                let typeBadge = '';
                if (p.auth_type === 'ide_bridge') {
                    typeBadge = '<span style="font-size: 11px; padding: 2px 7px; border-radius: 8px; background: rgba(59,130,246,0.15); color: #60a5fa;">IDE / 开发者环境</span>';
                } else if (p.auth_type === 'local_engine') {
                    typeBadge = '<span style="font-size: 11px; padding: 2px 7px; border-radius: 8px; background: rgba(34,197,94,0.15); color: #4ade80;">本地离线引擎</span>';
                } else {
                    typeBadge = '<span style="font-size: 11px; padding: 2px 7px; border-radius: 8px; background: rgba(245,158,11,0.15); color: #fbbf24;">官方直接 API</span>';
                }

                card.innerHTML = `
                    <div style="display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px;">
                        <div style="display: flex; align-items: center; gap: 8px;">
                            <span style="font-weight: 600; font-size: 14px;">${p.name}</span>
                            ${typeBadge}
                        </div>
                        <div style="display: flex; align-items: center; gap: 10px;">
                            <span id="test-status-${pKey}" style="font-size: 12px; color: var(--text-muted);">未测试</span>
                            <button class="btn btn-secondary" style="font-size: 11.5px; padding: 4px 10px;" onclick="testProviderConnection('${pKey}')">连通性测试 (Ping)</button>
                        </div>
                    </div>
                    <div style="font-size: 12px; color: var(--text-muted);">${p.description || ''}</div>
                    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 10px; margin-top: 4px;">
                        <div>
                            <span style="font-size: 11px; color: var(--text-muted);">服务端点 (Base URL)</span>
                            <input class="form-input" style="padding: 5px 8px; font-size: 12px; margin-top: 3px;" value="${p.base_url || ''}" onchange="updateProviderField('${pKey}', 'base_url', this.value)" />
                        </div>
                        <div>
                            <span style="font-size: 11px; color: var(--text-muted);">调用模型 (Model)</span>
                            <input class="form-input" style="padding: 5px 8px; font-size: 12px; margin-top: 3px;" value="${p.model || ''}" onchange="updateProviderField('${pKey}', 'model', this.value)" />
                        </div>
                        <div>
                            <span style="font-size: 11px; color: var(--text-muted);">访问凭证 (API Key / Token)</span>
                            <input class="form-input" type="password" placeholder="${p.api_key_masked || (p.auth_type === 'ide_bridge' ? '本地会话免填' : '填入官方 API Key')}" style="padding: 5px 8px; font-size: 12px; margin-top: 3px;" onchange="updateProviderField('${pKey}', 'api_key', this.value)" />
                        </div>
                    </div>
                `;
                listEl.appendChild(card);
            });
        }

        function updateProviderField(providerKey, field, val) {
            if (!globalComputeConfig || !globalComputeConfig.providers[providerKey]) return;
            globalComputeConfig.providers[providerKey][field] = val;
        }

        async function saveComputeMatrixSlots() {
            if (!globalComputeConfig) return;
            const pSlot = document.getElementById('slot-persona-provider').value;
            const lSlot = document.getElementById('slot-logic-provider').value;
            globalComputeConfig.active_slots = {
                persona_slot: pSlot,
                logic_slot: lSlot
            };
            try {
                const res = await fetch('/api/compute/config', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(globalComputeConfig)
                });
                const d = await res.json();
                if (d.success) {
                    showToast('双池槽位调度保存成功');
                } else {
                    showToast('保存失败: ' + (d.error || '未知错误'), 'error');
                }
            } catch (e) {
                showToast('保存异常: ' + e, 'error');
            }
        }

        async function saveComputeProvidersConfig() {
            if (!globalComputeConfig) return;
            try {
                const res = await fetch('/api/compute/config', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(globalComputeConfig)
                });
                const d = await res.json();
                if (d.success) {
                    showToast('Provider 算力配置已更新');
                    loadComputeMatrix();
                } else {
                    showToast('保存失败: ' + (d.error || '未知错误'), 'error');
                }
            } catch (e) {
                showToast('保存异常: ' + e, 'error');
            }
        }

        async function testCredentialLatency(poolType, idx) {
            const list = poolType === 'master' 
                ? (globalModelsConfig && globalModelsConfig.master_credentials) 
                : (globalModelsConfig && globalModelsConfig.function_credentials);
            if (!list || !list[idx]) return;
            const cred = list[idx];
            const latencyEl = document.getElementById(`cred-latency-${poolType}-${idx}`);
            if (latencyEl) {
                latencyEl.innerHTML = '<span class="spinner"></span> 测速中...';
            }
            const t0 = performance.now();
            try {
                const res = await fetch('/api/compute/test', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        provider_key: cred.provider || 'default',
                        api_key: cred.api_key || '',
                        base_url: cred.base_url || '',
                        model: cred.model || ''
                    })
                });
                const elapsed = Math.round(performance.now() - t0);
                const data = await res.json();
                cred.last_latency_ms = data.latency_ms || elapsed;
                if (latencyEl) {
                    if (data.ok || res.ok) {
                        latencyEl.innerHTML = `<span style="color: #059669; font-weight: 600;">延迟: ${cred.last_latency_ms}ms</span>`;
                    } else {
                        latencyEl.innerHTML = `<span style="color: #dc2626; font-weight: 500;">失败 (${data.error || '超时'})</span>`;
                    }
                }
            } catch (e) {
                const elapsed = Math.round(performance.now() - t0);
                cred.last_latency_ms = elapsed;
                if (latencyEl) {
                    latencyEl.innerHTML = `<span style="color: #64748b;">延迟: ${elapsed}ms</span>`;
                }
            }
        }

        async function testProviderConnection(pKey) {
            const statusEl = document.getElementById(`test-status-${pKey}`);
            if (statusEl) {
                statusEl.innerHTML = '<span style="color: var(--accent-cyan);">Testing...</span>';
            }
            try {
                const res = await fetch('/api/compute/test', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({provider_key: pKey})
                });
                const d = await res.json();
                if (d.ok) {
                    if (statusEl) statusEl.innerHTML = `<span style="color: var(--accent-green);">正常 (${d.latency_ms}ms)</span>`;
                } else {
                    if (statusEl) statusEl.innerHTML = `<span style="color: var(--accent-rose);">失败 (${d.error || '连接超时'})</span>`;
                }
            } catch (e) {
                if (statusEl) statusEl.innerHTML = `<span style="color: var(--accent-rose);">错误: ${e}</span>`;
            }
        }


        // 通用模块运行状态与健康探针异步检测

// Explicit Window Exports for Global Invocations
window.openCreditsModal = openCreditsModal;
window.closeCreditsModal = closeCreditsModal;
window.setQuickTopUp = setQuickTopUp;
window.submitCreditsTopUp = submitCreditsTopUp;
window.switchSettingsSubtab = switchSettingsSubtab;
window.switchPersonaSubtab = switchPersonaSubtab;
window.syncUpstreamModels = syncUpstreamModels;
window.saveCredentialsPoolConfig = saveCredentialsPoolConfig;
window.addCredentialRow = addCredentialRow;
window.renderCredentialsPoolUI = renderCredentialsPoolUI;
window.renderCredentialsPoolConfig = renderCredentialsPoolUI;
window.renderSingleCredentialPool = renderSingleCredentialPool;
window.updateCredentialField = updateCredentialField;
window.onCredentialAuthTypeChange = onCredentialAuthTypeChange;
window.onCredentialKeyInput = onCredentialKeyInput;
window.toggleKeyVisibility = toggleKeyVisibility;
window.testCredentialLatency = testCredentialLatency;
window.changeLanguage = changeLanguage;
window.submitChangePassword = submitChangePassword;
window.syncActiveModelToAllDisplays = syncActiveModelToAllDisplays;

