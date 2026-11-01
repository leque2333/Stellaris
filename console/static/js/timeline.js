// -*- coding: utf-8 -*-
// Stellaris - Timeline Stream Module

        async function loadTimelineData() {
            try {
                const resp = await fetch('/api/timeline');
                if (!resp.ok) return;
                const events = await resp.json();
                const fullContainer = document.getElementById('timeline-full-list');
                const pack = i18n[currentLang] || i18n['zh-CN'];
                if (fullContainer && Array.isArray(events)) {
                    fullContainer.innerHTML = events.map(renderTimelineCard).join('') || `<div style="color: var(--text-muted); font-size: 13px;">${pack.empty_timeline}</div>`;
                }
            } catch (e) {
                console.warn('Failed to load timeline events:', e);
            }
            await loadMemoryFacts();
        }
        window.loadTimelineData = loadTimelineData;

        async function loadMemoryFacts() {
            const box = document.getElementById('memory-facts-list');
            if (!box) return;
            refreshPendingFactCount();
            const subject = (document.getElementById('memory-fact-subject')?.value || '').trim();
            const asofRaw = document.getElementById('memory-fact-asof')?.value || '';
            const status = document.getElementById('memory-fact-status')?.value || 'active';
            let url = '/api/memory/facts?limit=200';
            if (asofRaw) {
                if (!subject) {
                    showToast('按时间查询时先填 person_id');
                    return;
                }
                const ts = Math.floor(new Date(asofRaw).getTime() / 1000);
                if (!Number.isFinite(ts)) {
                    showToast('时间格式不正确');
                    return;
                }
                url += '&subject_id=' + encodeURIComponent(subject) + '&as_of=' + ts;
            } else {
                url += '&status=' + encodeURIComponent(status);
                if (subject) url += '&subject_id=' + encodeURIComponent(subject);
            }
            try {
                const resp = await fetch(url);
                const data = await resp.json();
                if (!resp.ok || data.ok === false) throw new Error(data.error || '读取失败');
                const facts = data.facts || [];
                if (!facts.length) {
                    box.innerHTML = '<div class="conn-empty">还没有长期事实。对中枢说「记住……」会自动写入。</div>';
                    return;
                }
                box.innerHTML = `<table class="data-table">
                    <thead><tr><th>对象</th><th>事实</th><th>值</th><th>状态</th><th>有效时间</th><th>操作</th></tr></thead>
                    <tbody>${facts.map(renderMemoryFactRow).join('')}</tbody>
                </table>`;
                const subjectForGraph = subject || (facts[0] && facts[0].subject_id) || '';
                if (subjectForGraph) await loadMemoryGraph(subjectForGraph);
            } catch (e) {
                box.innerHTML = `<div class="conn-empty">读取事实失败：${escapeHtml(e.message || e)}</div>`;
            }
        }
        window.loadMemoryFacts = loadMemoryFacts;

        async function refreshPendingFactCount() {
            const el = document.getElementById('memory-pending-count');
            if (!el) return;
            try {
                const resp = await fetch('/api/memory/facts?status=pending&limit=200');
                const data = await resp.json();
                const n = (data.facts || []).length;
                el.textContent = n ? `待确认 ${n}` : '';
            } catch (e) {
                el.textContent = '';
            }
        }
        window.refreshPendingFactCount = refreshPendingFactCount;

        async function loadMemoryGraph(subjectId) {
            const box = document.getElementById('memory-graph-list');
            if (!box) return;
            const subject = subjectId || (document.getElementById('memory-fact-subject')?.value || '').trim();
            if (!subject) {
                box.innerHTML = '<div class="conn-empty">填 person_id 后查看关系。</div>';
                return;
            }
            try {
                const resp = await fetch('/api/memory/graph?subject_id=' + encodeURIComponent(subject));
                const data = await resp.json();
                if (!resp.ok || data.ok === false) throw new Error(data.error || '读取失败');
                const edges = data.edges || [];
                if (!edges.length) {
                    box.innerHTML = '<div class="conn-empty">还没有实体关系。</div>';
                    return;
                }
                box.innerHTML = `<table class="data-table">
                    <thead><tr><th>主体</th><th>关系</th><th>客体</th><th>有效时间</th></tr></thead>
                    <tbody>${edges.map(edge => `<tr>
                        <td>${escapeHtml(edge.subject_id || '')}</td>
                        <td>${escapeHtml(edge.predicate || '')}</td>
                        <td>${escapeHtml(edge.object_value || '')}</td>
                        <td class="conn-mono">${escapeHtml(edge.valid_at ? new Date(Number(edge.valid_at) * 1000).toLocaleString() : '-')}</td>
                    </tr>`).join('')}</tbody>
                </table>`;
            } catch (e) {
                box.innerHTML = `<div class="conn-empty">读取关系失败：${escapeHtml(e.message || e)}</div>`;
            }
        }
        window.loadMemoryGraph = loadMemoryGraph;

        function renderMemoryFactRow(fact) {
            const valid = fact.valid_at ? new Date(Number(fact.valid_at) * 1000).toLocaleString() : '-';
            const status = fact.status === 'active' ? '当前' : (
                fact.status === 'superseded' ? '已被替换' : (
                    fact.status === 'pending' ? '待确认' : fact.status
                )
            );
            const actions = fact.status === 'pending' ? `
                <button class="btn btn-secondary btn-xs" onclick="restoreMemoryFact('${escapeHtml(fact.fact_id)}')">确认</button>
                <button class="btn btn-subtle btn-xs" onclick="deleteMemoryFact('${escapeHtml(fact.fact_id)}')">拒绝</button>` : (fact.status === 'deleted' ? `
                <button class="btn btn-secondary btn-xs" onclick="restoreMemoryFact('${escapeHtml(fact.fact_id)}')">恢复</button>` : (fact.status === 'superseded' ? `
                <button class="btn btn-secondary btn-xs" onclick="restoreMemoryFact('${escapeHtml(fact.fact_id)}')">设为当前</button>` : `
                <button class="btn btn-secondary btn-xs" onclick="editMemoryFact('${escapeHtml(fact.fact_id)}')">修改</button>
                <button class="btn btn-subtle btn-xs" onclick="deleteMemoryFact('${escapeHtml(fact.fact_id)}')">删除</button>`));
            return `<tr>
                <td><div class="context-id">${escapeHtml(fact.subject_id || '')}</div></td>
                <td>${escapeHtml(fact.predicate || '')}</td>
                <td>${escapeHtml(fact.value || '')}</td>
                <td>${escapeHtml(status)}</td>
                <td class="conn-mono">${escapeHtml(valid)}</td>
                <td>${actions}</td>
            </tr>`;
        }

        async function editMemoryFact(factId) {
            const value = window.prompt('新的值');
            if (!value || !value.trim()) return;
            try {
                const resp = await fetch('/api/memory/facts', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ fact_id: factId, value: value.trim() })
                });
                const data = await resp.json();
                if (!resp.ok || data.ok === false) throw new Error(data.error || '保存失败');
                showToast('事实已更新');
                await loadMemoryFacts();
            } catch (e) {
                showToast('更新失败：' + (e.message || e));
            }
        }
        window.editMemoryFact = editMemoryFact;

        async function deleteMemoryFact(factId) {
            if (!window.confirm('删除这条事实？')) return;
            try {
                const resp = await fetch('/api/memory/facts?fact_id=' + encodeURIComponent(factId), { method: 'DELETE' });
                const data = await resp.json();
                if (!resp.ok || data.ok === false) throw new Error(data.error || '删除失败');
                showToast('事实已删除');
                await loadMemoryFacts();
            } catch (e) {
                showToast('删除失败：' + (e.message || e));
            }
        }
        window.deleteMemoryFact = deleteMemoryFact;

        async function restoreMemoryFact(factId) {
            if (!window.confirm('把这条历史事实恢复为当前？')) return;
            try {
                const resp = await fetch('/api/memory/facts/rollback', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ fact_id: factId })
                });
                const data = await resp.json();
                if (!resp.ok || data.ok === false) throw new Error(data.error || '恢复失败');
                showToast('事实已恢复为当前');
                await loadMemoryFacts();
            } catch (e) {
                showToast('恢复失败：' + (e.message || e));
            }
        }
        window.restoreMemoryFact = restoreMemoryFact;

        function renderTimelineCard(ev) {
            let tagClass = 'tag-core';
            const modRaw = String(ev.source_module || ev.event_type || '').toLowerCase();
            let moduleText = '中枢';

            if (modRaw.includes('astrbot_group')) {
                tagClass = 'tag-chat';
                moduleText = 'QQ群聊';
            } else if (modRaw.includes('astrbot_private')) {
                tagClass = 'tag-chat';
                moduleText = 'QQ私聊';
            } else if (modRaw === 'qq_chat' || modRaw === 'astrbot') {
                tagClass = 'tag-chat';
                moduleText = 'QQ';
            } else if (modRaw.includes('bili')) {
                tagClass = 'tag-bili';
                moduleText = 'B站';
            } else if (modRaw === 'cli_interaction' || modRaw === 'universal_proxy') {
                tagClass = 'tag-chat';
                moduleText = '会话';
            } else if (modRaw === 'model_call' || modRaw === 'llm_invoke') {
                tagClass = 'tag-core';
                moduleText = 'AI调用';
            } else if (modRaw === 'pc_bridge') {
                tagClass = 'tag-pc';
                moduleText = 'PC控制';
            } else if (ev.source_module || ev.event_type) {
                moduleText = ev.source_module || ev.event_type;
            }

            const rawActor = ev.actor_name || ev.source || '';
            const primary = String(ev.tag_primary || moduleText).trim();
            const secondary = String(ev.tag_secondary || rawActor || (moduleText === '中枢' ? '系统' : '交互')).trim();
            const permission = String(ev.tag_permission || '').trim().toLowerCase() || 'unknown';
            const platform = String(ev.platform || '').trim().toLowerCase();
            const displayName = String(ev.display_name || ev.archive_name || ev.uid || secondary || rawActor).trim();
            const isArchive = Boolean(ev.archive_id) || secondary.startsWith('arc_');
            const actorClass = isArchive ? `timeline-actor is-archive role-${permission}` : 'timeline-actor is-uid';
            const actorTitle = isArchive
                ? [ev.archive_id, permission].filter(Boolean).join(' · ')
                : [platform, ev.uid || secondary].filter(Boolean).join(':');
            const timeDisp = escapeHtml(ev.time_str || '');
            const narrDisp = escapeHtml(ev.narrative || ev.content || '');

            return `
                <div class="timeline-card">
                    <div class="timeline-header">
                        <span class="timeline-tag ${tagClass}">${escapeHtml(primary)}</span>
                        ${platform && platform !== primary.toLowerCase() ? `<span class="timeline-platform">${escapeHtml(platform)}</span>` : ''}
                        <span class="${actorClass}" title="${escapeHtml(actorTitle)}">${escapeHtml(displayName)}</span>
                        <span class="timeline-time">${timeDisp}</span>
                    </div>
                    <div class="timeline-body">${narrDisp}</div>
                </div>
            `;
        }

