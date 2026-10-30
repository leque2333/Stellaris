// -*- coding: utf-8 -*-
// Stellaris - Persona, Affection & Regex Rules Module

        function applyPersonaProfile(profile) {
            if (!profile) return;
            const nameEl = document.getElementById('persona-name-input');
            const baseEl = document.getElementById('persona-base-editor');
            const examplesEl = document.getElementById('persona-examples-editor');
            const otherEl = document.getElementById('persona-other-editor');
            const setIfEmpty = (el, value) => {
                if (el && !el.value.trim() && value != null) el.value = value;
            };
            setIfEmpty(nameEl, profile.name);
            setIfEmpty(baseEl, profile.persona);
            setIfEmpty(examplesEl, profile.dialogue_examples);
            setIfEmpty(otherEl, profile.other);
            // 兼容旧接口：只有整段 system_prompt 时放进人格设定
            if (baseEl && !baseEl.value.trim() && !profile.persona && profile.system_prompt) {
                baseEl.value = profile.system_prompt;
            }
        }

        async function savePersona() {
            const name = (document.getElementById('persona-name-input')?.value || '').trim();
            const persona = (document.getElementById('persona-base-editor')?.value || '').trim();
            const dialogue_examples = (document.getElementById('persona-examples-editor')?.value || '').trim();
            const other = (document.getElementById('persona-other-editor')?.value || '').trim();
            const pack = i18n[currentLang] || i18n['zh-CN'];
            if (!name) {
                showToast('请填写 AI 名称');
                return;
            }
            if (!persona) {
                showToast('请填写人格设定');
                return;
            }
            try {
                const res = await fetch('/api/persona', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name, persona, dialogue_examples, other })
                });
                const ret = await res.json();
                if (ret.success) {
                    showToast(pack.toast_prompt_saved);
                } else {
                    showToast(ret.error || 'Error');
                }
            } catch (e) {
                showToast('Error');
            }
        }

        // ------------------ 用户自定义温差系统与口语正则 ------------------
        window.personaRules = {
            affection_tiers: [
                {
                    id: "tier_cold",
                    min_score: 0,
                    max_score: 39,
                    stage_name: "初识与疏离（克制边界）",
                    attitude_hint: "对不熟的人克制客气，简短得体，保持分寸感与独立边界，不主动展开漫无目的的闲聊"
                },
                {
                    id: "tier_warm",
                    min_score: 40,
                    max_score: 79,
                    stage_name: "熟悉与朋友（自然随性）",
                    attitude_hint: "自然随和，亲切温和，真诚待人，带有日常朋友般的从容与陪伴感"
                },
                {
                    id: "tier_intimate",
                    min_score: 80,
                    max_score: 100,
                    stage_name: "极度信任与至交（亲近默契）",
                    attitude_hint: "非常熟悉亲昵，机敏打趣，言语松弛自如，坦率真诚不别扭；面对好感度 80 以上的人，厌世度几乎完全消失（仅留 2% 微妙的漫不经心游离感），活力值显著提高，喜欢在言语中抛出机敏打趣与熟人间的小互动，被识破时一声轻快的“嗯哼～”，展现出极度信任与依赖的温热内核。"
                }
            ],
            speech_regex_rules: [
                {
                    id: "rule_strip_action_brackets_zh",
                    name: "中文神态动作描写括号过滤",
                    pattern: "（[^）]*）",
                    replacement: "",
                    description: "剥离中文全角括号内的舞台神态描写、心理活动或非台词说明",
                    is_active: true
                },
                {
                    id: "rule_strip_action_brackets_en",
                    name: "英文动作描写括号过滤",
                    pattern: "\\([^\\)]*[一-龥][^\\)]*\\)",
                    replacement: "",
                    description: "剥离包含中文字符的半角英文括号内动作或神态描写",
                    is_active: true
                },
                {
                    id: "rule_strip_trailing_periods",
                    name: "句末句号彻底剔除",
                    pattern: "。+$|(?<!\\.)\\.(?!\\.)$",
                    replacement: "",
                    description: "去除台词末尾的句号，保持随性自然的真人呼吸感",
                    is_active: true
                }
            ]
        };

        async function ensurePersonaLoaded() {
            const baseEl = document.getElementById('persona-base-editor');
            if (baseEl && (!baseEl.value || !baseEl.value.trim())) {
                try {
                    const res = await fetch('/api/overview');
                    if (res.ok) {
                        const data = await res.json();
                        applyPersonaProfile(data.current_persona);
                        if (data.persona_rules && (data.persona_rules.affection_tiers || data.persona_rules.speech_regex_rules)) {
                            window.personaRules = data.persona_rules;
                            renderAffectionTiers();
                            renderSpeechRegexRules();
                        }
                    }
                } catch (e) {}
            }
        }

        function renderAffectionTiers() {
            const container = document.getElementById('affection-tiers-container');
            if (!container) return;
            const tiers = window.personaRules.affection_tiers || [];
            if (tiers.length === 0) {
                container.innerHTML = '<div style="color: var(--text-muted); font-size: 13px; padding: 12px;">暂无温差配置，点击上方按钮添加。</div>';
                return;
            }
            container.innerHTML = tiers.map((tier, idx) => `
                <div class="tier-card" style="background: #ffffff; border: none; border-radius: 12px; padding: 18px 20px; margin-bottom: 12px; box-shadow: none;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                        <div style="display: flex; align-items: center; gap: 10px;">
                            <span style="font-size: 12px; font-weight: 600; padding: 2px 8px; border-radius: 4px; background: rgba(15, 23, 42, 0.06); color: #0f172a;">阶段 ${idx + 1}</span>
                            <input class="form-input" style="font-size: 13px; font-weight: 600; padding: 5px 10px; width: 220px; border: none; background: rgba(15, 23, 42, 0.04); border-radius: 6px; color: #0f172a;" value="${escapeHtml(tier.stage_name || '')}" placeholder="阶段名称（如：初识与疏离）" onchange="window.personaRules.affection_tiers[${idx}].stage_name = this.value">
                        </div>
                        <button class="btn btn-secondary btn-xs" onclick="removeAffectionTier(${idx})" style="color: #dc2626;">删除此阶段</button>
                    </div>
                    <div class="form-grid" style="grid-template-columns: 120px 120px minmax(0, 1fr); gap: 14px; align-items: start;">
                        <div>
                            <label class="form-label" style="display: block; font-size: 11px; margin-bottom: 5px;">最小好感度 (Min)</label>
                            <input type="number" min="0" max="100" class="form-input" style="width: 100%;" value="${tier.min_score ?? 0}" onchange="window.personaRules.affection_tiers[${idx}].min_score = parseInt(this.value) || 0">
                        </div>
                        <div>
                            <label class="form-label" style="display: block; font-size: 11px; margin-bottom: 5px;">最大好感度 (Max)</label>
                            <input type="number" min="0" max="100" class="form-input" style="width: 100%;" value="${tier.max_score ?? 100}" onchange="window.personaRules.affection_tiers[${idx}].max_score = parseInt(this.value) || 100">
                        </div>
                        <div>
                            <label class="form-label" style="display: block; font-size: 11px; margin-bottom: 5px;">心理态度指引 (Attitude Prompt Guidance)</label>
                            <textarea class="form-input" style="width: 100%; min-height: 112px; font-size: 12px; line-height: 1.55; box-sizing: border-box;" placeholder="输入此好感度区间下智能体的态度与语气指引..." onchange="window.personaRules.affection_tiers[${idx}].attitude_hint = this.value">${escapeHtml(tier.attitude_hint || '')}</textarea>
                        </div>
                    </div>
                </div>
            `).join('');
        }

        function addAffectionTier() {
            if (!window.personaRules.affection_tiers) window.personaRules.affection_tiers = [];
            window.personaRules.affection_tiers.push({
                id: 'tier_' + Date.now(),
                min_score: 50,
                max_score: 75,
                stage_name: '新温差阶段',
                attitude_hint: '自然随和沟通，真诚待人'
            });
            renderAffectionTiers();
        }

        function removeAffectionTier(idx) {
            if (confirm('确认删除此温差阶段？')) {
                window.personaRules.affection_tiers.splice(idx, 1);
                renderAffectionTiers();
            }
        }

        async function saveAffectionTiers() {
            try {
                const res = await fetch('/api/persona/rules', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(window.personaRules)
                });
                const data = await res.json();
                if (data.success) {
                    showToast('好感度温差配置已保存生效');
                } else {
                    showToast(data.message || '保存失败');
                }
            } catch (e) {
                showToast('保存异常: ' + e);
            }
        }

        function renderSpeechRegexRules() {
            const container = document.getElementById('speech-regex-container');
            if (!container) return;
            const rules = window.personaRules.speech_regex_rules || [];
            if (rules.length === 0) {
                container.innerHTML = '<div style="color: var(--text-muted); font-size: 13px; padding: 12px;">暂无口语正则规则，点击上方按钮添加。</div>';
                return;
            }
            container.innerHTML = rules.map((r, idx) => `
                <div class="tier-card" style="background: #ffffff; border: none; border-radius: 12px; padding: 18px 20px; margin-bottom: 12px; box-shadow: none;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                        <div style="display: flex; align-items: center; gap: 12px;">
                            <label style="display: flex; align-items: center; gap: 6px; cursor: pointer; font-size: 12.5px; font-weight: 600;">
                                <input type="checkbox" ${r.is_active !== false ? 'checked' : ''} onchange="window.personaRules.speech_regex_rules[${idx}].is_active = this.checked; runLiveRegexTest();">
                                <span>启用</span>
                            </label>
                            <input class="form-input" style="font-size: 13px; font-weight: 600; padding: 5px 10px; width: 220px; border: none; background: rgba(15, 23, 42, 0.04); border-radius: 6px; color: #0f172a;" value="${escapeHtml(r.name || '')}" placeholder="规则名称" onchange="window.personaRules.speech_regex_rules[${idx}].name = this.value">
                        </div>
                        <button class="btn btn-secondary btn-xs" onclick="removeSpeechRegexRule(${idx})" style="color: #dc2626;">删除规则</button>
                    </div>
                    <div class="form-grid" style="grid-template-columns: 1fr 140px 1fr; gap: 12px; align-items: start;">
                        <div>
                            <label class="form-label" style="font-size: 11px;">正则表达式 Pattern</label>
                            <input class="form-input" style="font-family: monospace; font-size: 12px;" value="${escapeHtml(r.pattern || '')}" placeholder="如：。+$" onchange="window.personaRules.speech_regex_rules[${idx}].pattern = this.value; runLiveRegexTest();">
                        </div>
                        <div>
                            <label class="form-label" style="font-size: 11px;">替换目标 (Replacement)</label>
                            <input class="form-input" style="font-family: monospace; font-size: 12px;" value="${escapeHtml(r.replacement || '')}" placeholder="留空为直接剔除" onchange="window.personaRules.speech_regex_rules[${idx}].replacement = this.value; runLiveRegexTest();">
                        </div>
                        <div>
                            <label class="form-label" style="font-size: 11px;">规则说明与备注</label>
                            <input class="form-input" style="font-size: 12px;" value="${escapeHtml(r.description || '')}" placeholder="描述清洗目的..." onchange="window.personaRules.speech_regex_rules[${idx}].description = this.value">
                        </div>
                    </div>
                </div>
            `).join('');
            runLiveRegexTest();
        }

        function addSpeechRegexRule() {
            if (!window.personaRules.speech_regex_rules) window.personaRules.speech_regex_rules = [];
            window.personaRules.speech_regex_rules.push({
                id: 'rule_' + Date.now(),
                name: '新口语过滤规则',
                pattern: '。+$',
                replacement: '',
                description: '自定义口语清洗规则',
                is_active: true
            });
            renderSpeechRegexRules();
        }

        function removeSpeechRegexRule(idx) {
            if (confirm('确认删除此正则规则？')) {
                window.personaRules.speech_regex_rules.splice(idx, 1);
                renderSpeechRegexRules();
            }
        }

        async function saveSpeechRegexRules() {
            try {
                const res = await fetch('/api/persona/rules', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(window.personaRules)
                });
                const data = await res.json();
                if (data.success) {
                    showToast('口语正则清洗规则已保存生效');
                } else {
                    showToast(data.message || '保存失败');
                }
            } catch (e) {
                showToast('保存异常: ' + e);
            }
        }

        function runLiveRegexTest() {
            const inEl = document.getElementById('regex-test-input');
            const outEl = document.getElementById('regex-test-output');
            if (!inEl || !outEl) return;
            let text = inEl.value || '';
            const rules = window.personaRules.speech_regex_rules || [];
            
            // 保护代码块与链接
            const codeBlocks = [];
            text = text.replace(/```[\s\S]*?```/g, m => {
                codeBlocks.push(m);
                return `__CODE_BLOCK_${codeBlocks.length - 1}__`;
            });
            text = text.replace(/`[^`\r\n]+`/g, m => {
                codeBlocks.push(m);
                return `__CODE_BLOCK_${codeBlocks.length - 1}__`;
            });

            const links = [];
            text = text.replace(/\[[^\]]+\]\([^\)]+\)/g, m => {
                links.push(m);
                return `__MD_LINK_${links.length - 1}__`;
            });

            for (const r of rules) {
                if (r.is_active === false || !r.pattern) continue;
                try {
                    const reg = new RegExp(r.pattern, 'g');
                    text = text.replace(reg, r.replacement || '');
                } catch (e) {
                    // Ignore regex error in live sandbox
                }
            }

            // 还原代码块与链接
            links.forEach((l, i) => { text = text.replace(`__MD_LINK_${i}__`, l); });
            codeBlocks.forEach((c, i) => { text = text.replace(`__CODE_BLOCK_${i}__`, c); });

            outEl.value = text.trim();
        }


        async function savePatrolConfig() {
            if (!globalConfig) return;
            globalConfig.bilibili = globalConfig.bilibili || {};
            globalConfig.bilibili.daily_comment_limit = parseInt(document.getElementById('p-daily-limit').value) || 16;
            globalConfig.bilibili.max_comments_per_session = parseInt(document.getElementById('p-session-limit').value) || 8;
            globalConfig.bilibili.min_comment_interval_seconds = parseInt(document.getElementById('p-interval').value) || 360;
            globalConfig.bilibili.master_uid = document.getElementById('p-master-uid').value.trim();
            globalConfig.bilibili.master_qq = document.getElementById('p-master-qq').value.trim();
            globalConfig.bilibili.max_comment_body_len = parseInt(document.getElementById('p-body-len').value) || 40;
            
            const kw = document.getElementById('p-keywords').value;
            globalConfig.bilibili.keywords = kw.split(/[,，]/).map(s => s.trim()).filter(Boolean);

            const tk = document.getElementById('p-target-pool').value;
            globalConfig.bilibili.target_keyword_pool = tk.split(/[,，]/).map(s => s.trim()).filter(Boolean);

            const pack = i18n[currentLang] || i18n['zh-CN'];
            try {
                const res = await fetch('/api/patrol_config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(globalConfig)
                });
                const ret = await res.json();
                if (ret.success) {
                    showToast(pack.toast_saved);
                } else {
                    showToast('Error');
                }
            } catch (e) {
                showToast('Error');
            }
        }

        // 后台定时轮询：专职更新看板数字与流水表格，绝不触碰任何表单输入与选择，彻底解决回退问题

// Explicit Window Exports for Persona Module
window.applyPersonaProfile = applyPersonaProfile;
window.savePersona = savePersona;
window.ensurePersonaLoaded = ensurePersonaLoaded;
window.renderAffectionTiers = renderAffectionTiers;
window.addAffectionTier = addAffectionTier;
window.removeAffectionTier = removeAffectionTier;
window.saveAffectionTiers = saveAffectionTiers;
window.renderSpeechRegexRules = renderSpeechRegexRules;
window.addSpeechRegexRule = addSpeechRegexRule;
window.removeSpeechRegexRule = removeSpeechRegexRule;
window.saveSpeechRegexRules = saveSpeechRegexRules;
window.runLiveRegexTest = runLiveRegexTest;
