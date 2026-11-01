// -*- coding: utf-8 -*-
// Stellaris - App Lifecycle Startup Engine

let hubAppStarted = false;
async function startHubApp() {
    if (hubAppStarted) return;
    hubAppStarted = true;

    try {
        if (typeof applyI18n === 'function') applyI18n();
        if (typeof applyHeroStripTheme === 'function') applyHeroStripTheme();
    } catch (e) {
        console.warn('Pre-init error:', e);
    }

    // 立即并发拉取大盘初始数据与硬件占用，0秒极速呈现
    try {
        // 先拿账本统计，数字立即开始滚动
        if (typeof fetchTokenStats === 'function') await fetchTokenStats();
        if (typeof loadAccessPolicy === 'function') loadAccessPolicy();
        if (typeof loadApiSecurity === 'function') loadApiSecurity();
        if (typeof fetchSystemResources === 'function') fetchSystemResources();
        // 时间轴/模块/模型配置较重，延后加载，避免阻塞首屏数字动画
        setTimeout(() => {
            if (typeof loadInitialData === 'function') loadInitialData();
        }, 1200);
    } catch (e) {
        console.warn('App parallel init error:', e);
    }

    // 启动后台定时轮询
    if (typeof fetchSystemResources === 'function' && !resourceMonitorTimer) {
        resourceMonitorTimer = setInterval(fetchSystemResources, 3500);
    }
    if (typeof fetchTokenStats === 'function') {
        setInterval(fetchTokenStats, 5000);
    }

    // 直接打开档案页 / 连接页
    try {
        const hashRoutes = {
            '#archives': ['tab-archives', 'nav-item-archives'],
            '#identity': ['tab-identity', 'nav-item-identity'],
            '#tools': ['tab-tools', 'nav-item-tools'],
            '#tools-edit': ['tab-tools', 'nav-item-tools'],
            '#ledger': ['tab-ledger', null],
        };
        if (window.location.hash === '#logs') {
            setTimeout(() => {
                if (typeof openLogsTab === 'function') openLogsTab();
            }, 400);
            return;
        }
        // 旧书签兼容：记忆调参已经并入设置页
        if (window.location.hash === '#memory-tuning') {
            setTimeout(() => {
                if (typeof openSettingsTab === 'function') openSettingsTab();
                if (typeof switchSettingsSubtab === 'function') {
                    const btn = document.querySelector("#tab-settings .subtab-btn[onclick*='subtab-memory-tuning']");
                    switchSettingsSubtab('subtab-memory-tuning', btn);
                }
            }, 400);
            return;
        }

        const route = hashRoutes[window.location.hash];
        if (route && typeof navigateTab === 'function') {
            const nav = route[1] ? document.getElementById(route[1]) : null;
            navigateTab(route[0], nav);
        }
    } catch (e) {
        console.warn('Hash route error:', e);
    }
}

// 智能状态适配：无论 DOM 处于 loading 还是 interactive/complete，确保 100% 立即触发
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', startHubApp);
} else {
    startHubApp();
}
window.addEventListener('load', startHubApp);
