// $ / esc / relTime / showToast / showConfirm / icon 均由 app.js 提供
const $ = $id;

// ==================== CoverManager 封面加载器 ====================

const CoverManager = (function() {
    const MAX_CONCURRENCY = 10;
    const RESIDENCE_TIME = 50;
    const MAX_CACHE = 400;

    let activeRequests = 0;
    const pendingSet = new Set();
    const activeFetches = new Map();
    const coverCache = new Map();
    const visibilityMap = new WeakMap();

    const PLACEHOLDERS = {
        error: 'data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIxMDAlIiBoZWlnaHQ9IjEwMCUiPjxyZWN0IHdpZHRoPSIxMDAlIiBoZWlnaHQ9IjEwMCUiIGZpbGw9IiNlZWVlZWUiLz48L3N2Zz4=',
        loading: 'data:image/gif;base64,R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs='
    };

    async function processQueue() {
        if (activeRequests >= MAX_CONCURRENCY || pendingSet.size === 0) return;

        // FIFO 顺序，IntersectionObserver 已按可见性入队
        const targetEl = pendingSet.values().next().value;
        if (!targetEl || !visibilityMap.get(targetEl)) { pendingSet.delete(targetEl); return; }

        pendingSet.delete(targetEl);
        activeRequests++;

        const controller = new AbortController();
        activeFetches.set(targetEl, controller);

        try {
            if (!targetEl.dataset.apiPath) {
                targetEl.src = PLACEHOLDERS.error;
                targetEl.classList.add('loaded');
                return;
            }

            const response = await fetch(targetEl.dataset.apiPath, { signal: controller.signal });
            if (!response.ok) throw new Error('Fetch failed');

            const blob = await response.blob();
            const objectUrl = URL.createObjectURL(blob);

            if (coverCache.size >= MAX_CACHE) {
                const firstKey = coverCache.keys().next().value;
                URL.revokeObjectURL(coverCache.get(firstKey));
                coverCache.delete(firstKey);
            }
            coverCache.set(targetEl.dataset.cacheKey, objectUrl);

            if (visibilityMap.get(targetEl)) {
                targetEl.src = objectUrl;
                targetEl.classList.add('loaded');
            }
        } catch (e) {
            if (e.name !== 'AbortError' && visibilityMap.get(targetEl)) {
                targetEl.src = PLACEHOLDERS.error;
            }
        } finally {
            activeFetches.delete(targetEl);
            activeRequests--;
            requestAnimationFrame(processQueue);
        }
    }

    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            const imgEl = entry.target;
            if (entry.isIntersecting) {
                visibilityMap.set(imgEl, true);

                if (coverCache.has(imgEl.dataset.cacheKey)) {
                    imgEl.src = coverCache.get(imgEl.dataset.cacheKey);
                    imgEl.classList.add('loaded');
                    observer.unobserve(imgEl);
                    return;
                }

                setTimeout(() => {
                    if (visibilityMap.get(imgEl)) {
                        pendingSet.add(imgEl);
                        processQueue();
                    }
                }, RESIDENCE_TIME);
            } else {
                visibilityMap.set(imgEl, false);
                pendingSet.delete(imgEl);
                if (activeFetches.has(imgEl)) {
                    activeFetches.get(imgEl).abort();
                    activeFetches.delete(imgEl);
                }
            }
        });
    }, { rootMargin: '500px 0px' });

    return {
        observe: function(imgEl, apiPath, cacheKey) {
            imgEl.dataset.apiPath = apiPath;
            imgEl.dataset.cacheKey = cacheKey;
            imgEl.src = PLACEHOLDERS.loading;
            imgEl.classList.remove('loaded');
            observer.observe(imgEl);
        },
        reset: function() {
            pendingSet.clear();
            activeFetches.forEach(controller => controller.abort());
            activeFetches.clear();
        }
    };
})();

// ==================== 登录系统 ====================

async function checkAuth() {
    try {
        const res = await fetch('/api/auth');
        const data = await res.json();
        if (!data.need_auth || data.authed) return true;
        $('loginOverlay').style.display = 'flex';
        $('loginPw').focus();
        return false;
    } catch (e) { return false; }
}

async function doLogin() {
    const pw = $('loginPw').value;
    if (!pw) return;
    $('loginError').innerText = '';
    try {
        const res = await fetch('/api/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password: pw })
        });
        const data = await res.json();
        if (data.ok) {
            $('loginOverlay').style.display = 'none';
            initApp();
        } else {
            $('loginError').innerText = data.error || '密码错误';
            $('loginPw').value = '';
            $('loginPw').focus();
        }
    } catch (e) {
        $('loginError').innerText = '连接失败';
    }
}

// ==================== 全局状态 ====================

const state = {
    paths: [], comics: [], cur: { p: '', n: '', pgs: [] },
    idx: 0, dbl: true, rtl: true, uiTimer: null, token: 0,
    touchX: 0, lastTap: 0, isMobile: /Android|iPhone|iPad/i.test(navigator.userAgent),
    random: { pIdx: -1, cIdx: -1, allComics: [] },
    dlTimers: {},
    filterText: '',
    sortMode: 0,
    activeTag: '',
    activeTags: new Set(),   // 多选 tag
    activePaths: new Set(),  // 多选书库路径
    allTags: {},
    homepageTags: new Set(), // 主页显示标签白名单（只有在此集合里的标签才出现在主页）
    tagCategories: {},       // tag 分类 { catName: [tag1, tag2, ...] }
    ctxTarget: null,
    recycleTarget: null,
    jmHistory: [],
    activeJmDownloads: new Set(),  // 跟踪活跃的 JM 下载任务（跨面板开关保持）
    longPressFired: false,
    menuJustClosed: false,
    hoverPreview: localStorage.getItem('hoverPreviewOff') !== '1',  // 鼠标悬浮封面预览开关（记忆用户选择）
    readerOpen: false,
    appFullscreen: false,
    // 统一分页状态
    allFiltered: [],       // 合并后的全部过滤结果
    displayedCount: 0,     // 当前已渲染的数量
    isLoadingMore: false,  // 防止并发加载
    currentPage: 0,        // 当前可视页码（基于滚动位置计算）
};

const PER_PAGE = 60;       // 每次加载数量

const SORT_LABELS = [icon('calendar') + ' 最新', icon('calendar') + ' 最旧', icon('clock') + ' 最近'];
const SORT_MODES = ['mtime-desc', 'mtime-asc', 'lastRead-desc'];
const FAV_TAG = '收藏';  // virtual tag for data comparison

const api = async (ep, params = {}) => {
    const res = await fetch(`/api/${ep}?` + new URLSearchParams(params).toString());
    if (!res.ok) {
        let msg = `HTTP ${res.status}`;
        try { const e = await res.json(); if (e.error) msg = e.error; } catch {}
        throw new Error(msg);
    }
    return res.json();
};
const apiPost = async (ep, body) => {
    const res = await fetch(`/api/${ep}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    if (!res.ok) {
        let msg = `HTTP ${res.status}`;
        try { const e = await res.json(); if (e.error) msg = e.error; } catch {}
        throw new Error(msg);
    }
    return res.json();
};

// ==================== 初始化 ====================

async function init() {
    // 全局事件委托：替代所有 inline onclick，防止 JS 注入
    // 在 auth 检查前注册，确保登录页按钮可用
    document.addEventListener('click', e => {
        const el = e.target.closest('[data-action]');
        if (!el) return;
        const action = el.dataset.action;
        const a1 = el.dataset.arg1 || '';
        const a2 = el.dataset.arg2 || '';
        switch (action) {
            // 标签栏
            case 'toggleHomepageTag': toggleHomepageTag(a1); break;
            case 'clearAllHomepageTags': clearAllHomepageTags(); break;
            case 'toggleTagFilter': toggleTagFilter(a1); break;
            case 'openTagPicker': openTagPicker(); break;
            case 'closeTagPicker': closeTagPicker(); break;
            case 'toggleHomepageTagsExpand': toggleHomepageTagsExpand(); break;
            case 'cycleSort': cycleSort(); break;
            // 标签选择器
            case 'tagPickerToggle': tagPickerToggle(a1); break;
            case 'tagPickerClear': tagPickerClear(); break;
            case 'tagPickerScrollTo': tagPickerScrollTo(a1, el); break;
            // 标签管理
            case 'addTagDirect': addTagDirect(a1); break;
            case 'addNewTagWithCat': addNewTagWithCat(a1, a2); break;
            case 'addTag': addTag(); break;
            case 'removeTag': removeTag(a1); break;
            case 'openTagManager': openTagManager(); break;
            case 'closeTagManager': closeTagManager(); break;
            case 'closeTagModal': closeTagModal(); break;
            case 'toggleTagVisibility': toggleTagVisibility(a1); break;
            case 'filterByTagFromMgr': filterByTagFromMgr(a1); break;
            case 'createTagCategory': createTagCategory(); break;
            case 'deleteTagCategory': deleteTagCategory(a1); break;
            case 'renameTag': renameTag(a1); break;
            case 'deleteTag': deleteTag(a1); break;
            case 'tagMgrScrollTo': tagMgrScrollTo(a1, el); break;
            case 'tagMgrShowAll': tagMgrShowAll(); break;
            case 'tagMgrHideAll': tagMgrHideAll(); break;
            case 'tagMgrInvert': tagMgrInvert(); break;
            // 侧边栏 & 导航
            case 'toggleSidebar': toggleSidebar(); break;
            case 'closeSidebar': closeSidebar(); break;
            case 'toggleCollapse': toggleSidebarCollapse(); break;
            case 'toggleFullscreen': toggleAppFullscreen(); break;
            case 'scrollToTop': scrollToTop(); break;
            case 'togglePathFilter': togglePathFilter(parseInt(a1)); break;
            case 'clearPathFilter': clearPathFilter(); break;
            // 页码
            case 'goToPage': goToPage(parseInt(a1)); break;
            // JM 下载
            case 'openJmPanel': openJmPanel(); break;
            case 'closeJmPanel': closeJmPanel(); break;
            case 'toggleMinimizeJm': toggleMinimizeJm(); break;
            case 'doJmSearch': doJmSearch(); break;
            case 'jmHistSearch': $('jmInput').value = a1; doJmSearch(); break;
            case 'startJmDownload': startJmDownload(el.dataset.id, el.dataset.name); break;
            case 'toggleDlPreview': toggleDlPreview(); break;
            case 'toggleHoverPreview': toggleHoverPreview(); break;
            case 'openDlStatus': openDlStatus(); break;
                        case 'sortByLastRead': sortByLastRead(); break;
            case 'closeDlStatus': closeDlStatus(); break;
            // 阅读器
            case 'closeReader': closeReader(); break;
            case 'toggleDir': toggleDir(); break;
            case 'toggleMode': toggleMode(); break;
            // 随机 & 其他
            case 'randomComic': randomComic(); break;
            case 'closeRandom': closeRandom(); break;
            case 'openRandomComic': openRandomComic(); break;
            case 'rerollRandom': rerollRandom(); break;
            case 'cleanDelComics': cleanDelComics(); break;
            case 'resetAllProgress': resetAllProgress(); break;
            // 回收站
            case 'openRecycleBin': openRecycleBin(); break;
            case 'closeRecycleBin': closeRecycleBin(); break;
            case 'cleanAllRecycle': cleanAllRecycle(); break;
            case 'recycleRestore': recycleRestore(); break;
            case 'recycleDelete': recycleDelete(); break;
            // 右键菜单 & 详情面板
            case 'ctxOpenDetail': ctxOpenDetail(); break;
            case 'ctxToggleDel': ctxToggleDel(); break;
            case 'detailToggleFav': detailToggleFav(); break;
            case 'detailToggleDel': detailToggleDel(); break;
            case 'detailResetProgress': detailResetProgress(); break;
            case 'detailDeleteComic': detailDeleteComic(); break;
            // 登录
            case 'doLogin': doLogin(); break;
        }
    });
    const authed = await checkAuth();
    if (authed) initApp();
}

async function initApp() {
    // 加载标签管理配置
    await loadTagConfig();

    const data = await api('config');
    state.paths = data.libraryPaths || [];
    renderNavLinks();
    $('sortBtn').innerHTML = SORT_LABELS[state.sortMode];

    // 并行加载所有库；单个库出错不影响其他库，把失败的记下来提示用户
    const results = await Promise.all(
        state.paths.map((p, i) => api('comics', { path: p })
            .then(res => ({ i, comics: res.comics || [] }))
            .catch(e => ({ i, comics: [], failedPath: p, reason: e.message })))
    );
    const failedPaths = results.filter(r => r.failedPath)
        .map(r => ({ path: r.failedPath, reason: r.reason }));
    results.forEach(r => { state.comics[r.i] = r.comics; });

    applyFilters();
    renderShelfEmpty(failedPaths);
    await loadStats();
    updateProgressButton();
    // 主题切换已由 app.js 的 toggleTheme 处理

    // 事件委托：书架区域的点击/右键/长按/悬浮
    setupBookshelfDelegation();

    initCoverSystem();
    setupInfiniteScroll();
    initSidebarCollapse();
    updateHoverPreviewBtn();
    initPageSlider();

    // 全局点击：关闭侧边栏、右键菜单、弹窗
    const closeMenus = (e) => {
        const sidebar = $('sidebar');
        const menuBtn = document.querySelector('.menu-btn');
        if (sidebar.classList.contains('open') && !sidebar.contains(e.target) && !menuBtn.contains(e.target)) {
            closeSidebar();
            e.stopPropagation();
            e.preventDefault();
            return;
        }
        if (!e.target.closest('.context-menu')) {
            $('contextMenu').classList.add('hidden');
            $('recycleMenu').classList.add('hidden');
        }
        if (e.target === $('randomOverlay')) closeRandom();
        if (e.target === $('jmOverlay')) closeJmPanel();
        if (e.target === $('dlOverlay')) closeDlStatus();
        if (e.target === $('tagOverlay')) closeTagModal();
        if (e.target === $('tagMgrOverlay')) closeTagManager();
        if (e.target === $('tagPickerOverlay')) closeTagPicker();
        if (e.target === $('recycleOverlay')) closeRecycleBin();
    };
    document.addEventListener('click', (e) => {
        if (state.longPressFired) { state.longPressFired = false; return; }
        closeMenus(e);
    }, true);

    // 手机端 touch 兜底：长按呼出菜单后，tap 其他区域即关
    document.addEventListener('touchstart', (e) => {
        const ctx = $('contextMenu');
        if (!ctx.classList.contains('hidden') && !e.target.closest('.context-menu')) {
            ctx.classList.add('hidden');
            // 阻止后续 click 冒泡到卡片，避免误触进入阅读
            e.preventDefault();
        }
    }, { passive: false });

    // select 元素 change 事件委托
    document.addEventListener('change', e => {
        const el = e.target.closest('[data-action]');
        if (!el) return;
        const action = el.dataset.action;
        const a1 = el.dataset.arg1 || '';
        if (action === 'moveTagToCatSelect') moveTagToCat(a1, el.value);
    });

    // ESC 关闭弹窗
    document.addEventListener('keydown', e => {
        if (!$('reader').classList.contains('hidden')) {
            if (e.key === 'ArrowRight' || e.key === 'ArrowDown' || e.key === ' ' || e.key === 'd' || e.key === 'D') { e.preventDefault(); turnPage(state.rtl ? -1 : 1); return; }
            if (e.key === 'ArrowLeft' || e.key === 'ArrowUp' || e.key === 'a' || e.key === 'A') { e.preventDefault(); turnPage(state.rtl ? 1 : -1); return; }
            if (e.key === 'Escape' || e.key === 'q' || e.key === 'Q') { closeReader(); return; }
        }
        if (e.key === 'e' || e.key === 'E') { toggleAppFullscreen(); return; }
        if (e.key === 'Escape') {
            if (!$('contextMenu').classList.contains('hidden')) { $('contextMenu').classList.add('hidden'); return; }
            if (!$('tagOverlay').classList.contains('hidden')) { closeTagModal(); return; }
            if (!$('tagMgrOverlay').classList.contains('hidden')) { closeTagManager(); return; }
            if (!$('tagPickerOverlay').classList.contains('hidden')) { closeTagPicker(); return; }
            if (!$('jmOverlay').classList.contains('hidden')) { closeJmPanel(); return; }
            if (!$('randomOverlay').classList.contains('hidden')) { closeRandom(); return; }
            if (!$('recycleOverlay').classList.contains('hidden')) { closeRecycleBin(); return; }
        }
    });

    document.addEventListener('mouseleave', e => { if (e.clientY <= 0 || e.clientX <= 0) hidePreview(); });

    window.addEventListener('popstate', e => {
        if (state.readerOpen) closeReader(true);
    });

    // 恢复下载状态
    initDownloadRecovery();

    // 阅读器事件绑定（必须在 DOM 就绪后执行）
    const rc = $('readerContent');
    if (rc) {
        rc.onmousedown = e => { if (!state.isMobile && e.button === 0) handleAction(e.clientX); };
        rc.ontouchstart = e => { state.touchX = e.touches[0].clientX; };
        rc.ontouchend = e => {
            const diff = state.touchX - e.changedTouches[0].clientX;
            if (Math.abs(diff) > 60) turnPage(diff > 0 ? (state.rtl ? -1 : 1) : (state.rtl ? 1 : -1));
            else handleAction(e.changedTouches[0].clientX);
        };
    }
}

// ==================== 下载状态恢复 ====================

async function initDownloadRecovery() {
    try {
        const data = await api('download/history');
        const activeIds = (data.active || []).map(t => t.id);
        activeIds.forEach(jid => state.activeJmDownloads.add(jid));
        if (activeIds.length > 0) {
            updateDownloadBadge(activeIds.length);
            activeIds.forEach(jid => {
                if (!state.dlTimers[jid]) pollDownloadStatusGlobal(jid);
            });
        }
        // 有历史记录时也刷新显示
        if ((data.completed && data.completed.length) || (data.errors && data.errors.length)) {
            renderDownloadHistory();
        }
    } catch (e) { /* ignore */ }
}

function updateDownloadBadge(count) {
    let badge = $('downloadBadge');
    if (!badge) {
        // 在下载按钮上创建角标
        const dlBtn = document.querySelector('.btn-download');
        if (dlBtn) {
            badge = document.createElement('span');
            badge.id = 'downloadBadge';
            badge.style.cssText = 'position:absolute;top:-6px;right:-6px;background:#e74c3c;color:white;font-size:10px;font-weight:700;min-width:18px;height:18px;border-radius:9px;display:flex;align-items:center;justify-content:center;padding:0 4px;box-shadow:0 2px 6px rgba(231,76,60,0.4);z-index:5;pointer-events:none;animation:badgePop 0.3s cubic-bezier(0.34,1.56,0.64,1);';
            dlBtn.style.position = 'relative';
            dlBtn.appendChild(badge);
        }
    }
    if (badge) {
        if (count > 0) {
            badge.textContent = count;
            badge.style.display = 'flex';
        } else {
            badge.style.display = 'none';
        }
    }
}

// showToast 已由 app.js 提供

function refreshBookshelfIncremental() {
    // 增量刷新：重新加载所有书库数据，保持当前滚动位置和筛选状态
    const bookshelf = $('bookshelf');
    const savedScrollTop = bookshelf.scrollTop;
    
    Promise.all(
        state.paths.map((p, i) => api('comics', { path: p }).then(res => ({ i, comics: res.comics || [] })))
    ).then(results => {
        results.forEach(r => { state.comics[r.i] = r.comics; });
        state.allFiltered = [];
        state.displayedCount = 0;
        const grid = $('grid-all');
        grid.innerHTML = '';
        loadMoreItems();
        // 恢复滚动位置
        bookshelf.scrollTop = savedScrollTop;
        loadStats();
        // 下载完成后端会动标签分类（作者归入「艺术家」），顺手取一次最新的，
        // 免得本地这份过期副本在下次保存时把它覆盖掉。
        loadTagConfig().then(renderTagBar);
    }).catch(() => {});
}

function pollDownloadStatusGlobal(jmId) {
    if (state.dlTimers[jmId]) clearInterval(state.dlTimers[jmId]);
    state.dlTimers[jmId] = setInterval(async () => {
        try {
            const data = await api('download/status', { id: jmId });
            if (data.status === 'none' || data.status === 'done' || data.status === 'error') {
                clearInterval(state.dlTimers[jmId]);
                delete state.dlTimers[jmId];
                state.activeJmDownloads.delete(jmId);
                updateDownloadBadge(state.activeJmDownloads.size);
            }
        } catch (e) { }
    }, 3000);
}

// ==================== 事件委托（书架） ====================

function setupBookshelfDelegation() {
    const bookshelf = $('bookshelf');

    // 用事件委托统一处理点击、右键、长按
    let longTimer = null;
    let longTarget = null;

    bookshelf.addEventListener('touchstart', e => {
        const card = e.target.closest('.comic-card');
        if (!card) return;
        // 菜单打开时不触发长按
        if (!$('contextMenu').classList.contains('hidden')) return;
        state.longPressFired = false;
        longTarget = card;
        longTimer = setTimeout(() => {
            state.longPressFired = true;
            hidePreview();
            const pIdx = parseInt(card.dataset.pidx);
            handleCardContext(e.touches[0], pIdx, card.dataset.name);
        }, 500);
    }, { passive: true });

    const cancelLong = () => { clearTimeout(longTimer); longTarget = null; };
    bookshelf.addEventListener('touchend', cancelLong);
    bookshelf.addEventListener('touchmove', cancelLong);
    bookshelf.addEventListener('touchcancel', cancelLong);

    bookshelf.addEventListener('click', e => {
        const card = e.target.closest('.comic-card');
        if (!card) return;
        // 操作按钮点击
        if (e.target.closest('.card-action-btn')) {
            e.stopPropagation();
            handleCardContext(e, parseInt(card.dataset.pidx), card.dataset.name);
            return;
        }
        handleCardClick(e, parseInt(card.dataset.pidx), parseInt(card.dataset.cidx));
    });

    bookshelf.addEventListener('contextmenu', e => {
        const card = e.target.closest('.comic-card');
        if (!card) return;
        handleCardContext(e, parseInt(card.dataset.pidx), card.dataset.name);
    });

    // PC 悬浮预览（事件委托）
    if (!state.isMobile) {
        let hoverTimer = null;
        let hoverCard = null;
        const cancelHover = () => { clearTimeout(hoverTimer); hoverTimer = null; hoverCard = null; hidePreview(); };
        bookshelf.addEventListener('mouseenter', e => {
            const card = e.target.closest('.comic-card');
            if (!card) return;
            cancelHover();  // 进入新卡片前，先取消前一个待触发的预览
            hoverCard = card;
            hoverTimer = setTimeout(() => {
                if (!state.hoverPreview) return;  // 用户已关闭悬浮预览
                if (!$('contextMenu').classList.contains('hidden')) return;
                if (!$('randomOverlay').classList.contains('hidden')) return;
                if (!$('jmOverlay').classList.contains('hidden')) return;
                if (!$('tagOverlay').classList.contains('hidden')) return;
                if (hoverCard !== card) return;  // 鼠标已移到别的卡片，跳过
                showPreview(card, e);
            }, 500);
        }, true);
        bookshelf.addEventListener('mouseleave', e => {
            const card = e.target.closest('.comic-card');
            if (card && card === hoverCard) {
                cancelHover();
            }
        }, true);
    }
}

// ==================== 管理栏 ====================

function onFilterSearch() {
    state.filterText = $('filterSearch').value.trim().toLowerCase();
    invalidateCache();
    applyFilters();
}

function cycleSort() {
    state.sortMode = (state.sortMode + 1) % SORT_MODES.length;
    $('sortBtn').innerHTML = SORT_LABELS[state.sortMode];
    invalidateCache();
    applyFilters();
}
function sortByLastRead() {
    // 切换：已在最近阅读模式则恢复默认，否则进入最近阅读
    state.sortMode = (state.sortMode === 2) ? 0 : 2;
    $('sortBtn').innerHTML = SORT_LABELS[state.sortMode];
    // 按钮高亮状态
    const btn = document.querySelector('[data-action="sortByLastRead"]');
    if (btn) btn.classList.toggle('active', state.sortMode === 2);
    invalidateCache();
    applyFilters();
}

async function loadStats() {
    try {
        const stats = await api('stats');
        state.allTags = stats.tags || {};
        // 收藏作为虚拟 tag
        if (stats.fav > 0) state.allTags[FAV_TAG] = stats.fav;
        else delete state.allTags[FAV_TAG];
        renderTagBar();
        updateTagPickerBtn();
        updateSidebarStats(stats);
    } catch (e) { }
}

function updateSidebarStats(stats) {
    const delCount = (state.allTags || {})['del'] || 0;
    $('sidebarStats').innerHTML = `
        <div class="sidebar-stat"><div class="sidebar-stat-num">${stats.total}</div><div class="sidebar-stat-label">全部</div></div>
        <div class="sidebar-stat"><div class="sidebar-stat-num">${stats.fav}</div><div class="sidebar-stat-label">收藏</div></div>
        <div class="sidebar-stat"><div class="sidebar-stat-num">${Object.keys(stats.tags || {}).length}</div><div class="sidebar-stat-label">标签</div></div>
    `;
    // 侧边栏下载状态
    let dlIndicator = $('sidebarDlIndicator');
    if (!dlIndicator) {
        dlIndicator = document.createElement('div');
        dlIndicator.id = 'sidebarDlIndicator';
        dlIndicator.style.cssText = 'padding:8px 16px;margin:0 8px 4px;border-radius:8px;font-size:11px;font-weight:600;display:none;align-items:center;gap:6px;background:rgba(91,106,191,0.12);color:#5b6abf;border:1px solid rgba(91,106,191,0.2);';
        const sidebar = $('sidebar');
        const firstSection = sidebar.querySelector('.sidebar-section');
        if (firstSection) sidebar.insertBefore(dlIndicator, firstSection);
    }
    const activeDlCount = state.activeJmDownloads.size;
    if (activeDlCount > 0) {
        dlIndicator.innerHTML = `<div class="dl-spinner" style="width:12px;height:12px;border-width:2px"></div> 正在下载 ${activeDlCount} 本`;
        dlIndicator.style.display = 'flex';
    } else {
        dlIndicator.style.display = 'none';
    }
    const btn = $('btnCleanDel');
    if (delCount > 0) {
        btn.classList.remove('nav-item-disabled');
        $('delCount').textContent = delCount;
    } else {
        btn.classList.add('nav-item-disabled');
    }
    // 禁用/启用重置阅读进度按钮
    updateProgressButton();
}

function updateProgressButton() {
    const hasProgress = state.comics.some(list => list && list.some(c => c.lastRead || c.readProgress));
    $('btnResetProgress').classList.toggle('nav-item-disabled', !hasProgress);
}

// ==================== 标签管理配置（跨端同步） ====================

async function loadTagConfig() {
    try {
        const cfg = await api('tag-config');
        state.homepageTags = new Set(cfg.homepageTags || []);
        state.tagCategories = cfg.tagCategories || {};
    } catch (e) { /* ignore */ }
}

async function saveTagConfig() {
    try {
        await apiPost('tag-config', {
            homepageTags: [...state.homepageTags],
            tagCategories: state.tagCategories,
        });
    } catch (e) { console.warn('保存标签配置失败', e); }
}

function renderTagBar() {
    renderHomepageTags();
    renderActiveTags();
}

function renderHomepageTags() {
    const bar = $('homepageTagsBar');
    if (!bar) return;
    // 记住当前展开状态，重建 DOM 后恢复
    const wasExpanded = bar.classList.contains('expanded');
    // 只显示在 homepageTags 白名单中的标签（按数量降序）
    const visibleTags = Object.entries(state.allTags)
        .filter(([t]) => t !== 'del' && t !== FAV_TAG && state.homepageTags.has(t))
        .sort((a, b) => b[1] - a[1]);
    // 收藏始终显示在最前面
    const favCount = state.allTags[FAV_TAG] || 0;
    const favChip = favCount > 0
        ? `<button class="homepage-tag-chip ${state.activeTags.has(FAV_TAG) ? 'active' : ''}" data-action="toggleHomepageTag" data-arg1="${esc(FAV_TAG)}">${icon('star')} 收藏<span class="ht-count">${favCount}</span></button>`
        : '';
    // "全部"按钮：无选中 tag 时高亮
    const allActive = state.activeTags.size === 0;
    const allChip = `<button class="homepage-tag-chip homepage-tag-all ${allActive ? 'active' : ''}" data-action="clearAllHomepageTags">${icon('bookOpen')} 全部</button>`;
    const chips = allChip + favChip + visibleTags.map(([t, count]) => {
        const isActive = state.activeTags.has(t);
        return `<button class="homepage-tag-chip ${isActive ? 'active' : ''}" data-action="toggleHomepageTag" data-arg1="${esc(t)}">${esc(t)}<span class="ht-count">${count}</span></button>`;
    }).join('');
    bar.innerHTML = `<div class="homepage-tags-scroll">${chips}</div><button class="homepage-tags-expand" id="homepageTagsExpand" data-action="toggleHomepageTagsExpand">${icon('moreH')}</button>`;
    // 恢复展开状态
    if (wasExpanded) {
        bar.classList.add('expanded');
        const btn = $('homepageTagsExpand');
        if (btn) btn.innerHTML = icon('x');
    }
    updateHomepageTagsExpandBtn();
    requestAnimationFrame(() => updateHomepageTagsExpandBtn());
}

function clearAllHomepageTags() {
    state.activeTags.clear();
    state.activeTag = '';
    renderHomepageTags();
    renderActiveTags();
    updateTagPickerBtn();
    invalidateCache();
    applyFilters();
}

function updateHomepageTagsExpandBtn() {
    const scroll = document.querySelector('.homepage-tags-scroll');
    const btn = $('homepageTagsExpand');
    if (!scroll || !btn) return;
    const bar = $('homepageTagsBar');
    const isExpanded = bar && bar.classList.contains('expanded');
    const overflow = scroll.scrollHeight > scroll.clientHeight + 5 || scroll.scrollWidth > scroll.clientWidth + 5;
    if (overflow || isExpanded) {
        btn.style.display = '';
    } else {
        btn.style.display = 'none';
    }
}

function toggleHomepageTagsExpand() {
    const bar = $('homepageTagsBar');
    const btn = $('homepageTagsExpand');
    if (!bar || !btn) return;
    const expanded = bar.classList.toggle('expanded');
    btn.innerHTML = expanded ? icon('x') : icon('moreH');
    // 关闭折叠后，如果不溢出则隐藏按钮
    if (!expanded) {
        requestAnimationFrame(() => updateHomepageTagsExpandBtn());
    }
}

function toggleHomepageTag(tag) {
    toggleTagFilter(tag);
}

function renderActiveTags() {
    const bar = $('activeTagsBar');
    if (!bar) return;
    if (state.activeTags.size === 0) {
        bar.innerHTML = '';
        return;
    }
    bar.innerHTML = [...state.activeTags].map(t =>
        `<button class="active-tag-pill" data-action="toggleTagFilter" data-arg1="${esc(t)}">${esc(t)} ${icon('x')}</button>`
    ).join('');
}

// ==================== 标签选择器（主页） ====================

function openTagPicker() {
    $('tagPickerOverlay').classList.remove('hidden');
    $('tagPickerSearch').value = '';
    requestAnimationFrame(() => {
        $('tagPickerOverlay').classList.add('visible');
        $('tagPickerModal').classList.add('animate-in');
    });
    renderTagPicker();
}

function closeTagPicker() {
    $('tagPickerOverlay').classList.remove('visible');
    setTimeout(() => {
        $('tagPickerOverlay').classList.add('hidden');
        $('tagPickerModal').classList.remove('animate-in');
    }, 300);
    invalidateCache();
    applyFilters();
    renderHomepageTags();
    renderActiveTags();
    updateTagPickerBtn();
}

function updateTagPickerBtn() {
    const btn = $('tagPickerBtn');
    if (!btn) return;
    if (state.activeTags.size > 0) {
        btn.innerHTML = `${icon('tag')} 标签(${state.activeTags.size})`;
        btn.classList.add('active');
    } else {
        btn.innerHTML = `${icon('tag')} 标签`;
        btn.classList.remove('active');
    }
}

function renderTagPicker() {
    const search = ($('tagPickerSearch').value || '').trim().toLowerCase();
    const keywords = search.split(/[\s,，]+/).filter(Boolean);
    const allTags = Object.entries(state.allTags)
        .filter(([t]) => t !== 'del' && t !== FAV_TAG)
        .sort((a, b) => b[1] - a[1]);
    const cats = state.tagCategories || {};

    const tagToCat = {};
    for (const [cat, tags] of Object.entries(cats)) {
        for (const t of tags) tagToCat[t] = cat;
    }

    const matchTag = (t) => {
        if (!keywords.length) return true;
        const lower = t.toLowerCase();
        return keywords.every(k => lower.includes(k));
    };

    // 收藏始终放在最前面
    const favCount = state.allTags[FAV_TAG] || 0;
    let favSection = '';
    if (favCount > 0 && matchTag(FAV_TAG)) {
        const sel = state.activeTags.has(FAV_TAG);
        favSection = `<div class="tagpicker-section">
            <div class="tagpicker-grid"><button class="tagpicker-chip ${sel ? 'selected' : ''}" data-action="tagPickerToggle" data-arg1="${esc(FAV_TAG)}">
                <span class="tagpicker-chip-dot ${sel ? 'on' : ''}"></span>${icon('star')} 收藏<span class="tagpicker-chip-count">${favCount}</span>
            </button></div>
        </div>`;
    }

    // 按分类分组
    const grouped = {};
    const uncategorized = [];
    for (const [t, count] of allTags) {
        if (!matchTag(t)) continue;
        const cat = tagToCat[t];
        if (cat) {
            if (!grouped[cat]) grouped[cat] = [];
            grouped[cat].push({ tag: t, count });
        } else {
            uncategorized.push({ tag: t, count });
        }
    }

    // 分类导航栏
    const catNames = Object.keys(grouped);
    let catNav = '';
    if (catNames.length > 1 || uncategorized.length) {
        catNav = `<div class="tagpicker-cat-nav">
            <button class="tagpicker-cat-btn active" data-action="tagPickerScrollTo" data-arg1="all">全部</button>
            ${catNames.map(c => `<button class="tagpicker-cat-btn" data-action="tagPickerScrollTo" data-arg1="${esc(c)}">${esc(c)}</button>`).join('')}
            ${uncategorized.length ? `<button class="tagpicker-cat-btn" data-action="tagPickerScrollTo" data-arg1="__uncat__">未分类</button>` : ''}
        </div>`;
    }
    $('tagPickerCats').innerHTML = catNav;

    let html = favSection;
    const renderSection = (catName, items, id) => {
        if (!items.length) return '';
        return `<div class="tagpicker-section" id="tp-${id}">
            <div class="tagpicker-section-title">${id === '__uncat__' ? '未分类' : esc(catName)}</div>
            <div class="tagpicker-grid">${items.map(i => {
                const sel = state.activeTags.has(i.tag);
                return `<button class="tagpicker-chip ${sel ? 'selected' : ''}" data-action="tagPickerToggle" data-arg1="${esc(i.tag)}">
                    <span class="tagpicker-chip-dot ${sel ? 'on' : ''}"></span>${esc(i.tag)}<span class="tagpicker-chip-count">${i.count}</span>
                </button>`;
            }).join('')}</div>
        </div>`;
    };

    for (const [cat, items] of Object.entries(grouped)) {
        html += renderSection(cat, items, cat);
    }
    if (uncategorized.length) {
        html += renderSection('', uncategorized, '__uncat__');
    }

    if (!html) html = '<div class="tagpicker-empty">没有匹配的标签</div>';
    $('tagPickerBody').innerHTML = html;
}

function tagPickerToggle(tag) {
    if (state.activeTags.has(tag)) state.activeTags.delete(tag);
    else state.activeTags.add(tag);
    state.activeTag = state.activeTags.size === 1 ? [...state.activeTags][0] : '';
    renderTagPicker();
}

function tagPickerClear() {
    state.activeTags.clear();
    state.activeTag = '';
    renderTagPicker();
}

function tagPickerScrollTo(id, btn) {
    // 高亮分类按钮
    document.querySelectorAll('.tagpicker-cat-btn').forEach(b => b.classList.remove('active'));
    if (btn) btn.classList.add('active');
    if (id === 'all') {
        $('tagPickerBody').scrollTo({ top: 0, behavior: 'smooth' });
        return;
    }
    const el = document.getElementById('tp-' + id);
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// ==================== 清理待删除漫画 ====================

// showConfirm 已由 app.js 提供

async function cleanDelComics() {
    const delList = getDelComics();
    if (!delList.length) return;

    showConfirm(
        icon('trash'),
        `清理 ${delList.length} 本待删除漫画？`,
        '文件将被永久删除，无法恢复。',
        `确认删除 ${delList.length} 本`,
        async () => {
            const res = await apiPost('clean-del', {});
            if (res.ok) {
                delList.forEach(item => {
                    const list = state.comics[item.pIdx] || [];
                    const idx = list.findIndex(c => c.name === item.name);
                    if (idx >= 0) list.splice(idx, 1);
                });
                state.random.allComics = [];
                applyFilters();
                loadStats();
                showConfirm(icon('checkCircle'), `已删除 ${res.deleted} 本漫画`, '文件已永久删除', '好的', () => {});
            } else {
                showConfirm(icon('xCircle'), '清理失败', res.error || '未知错误', '关闭', () => {});
            }
        }
    );
}

// ==================== 重置阅读进度 ====================

function resetAllProgress() {
    showConfirm(icon('bookOpen'), '重置所有漫画的阅读进度？', '将清除所有阅读记录和页码进度，无法恢复。', '确认重置', async () => {
        try {
            const res = await apiPost('reset-progress', {});
            if (res.ok) {
                // 清除本地缓存
                for (const list of state.comics) {
                    if (list) list.forEach(c => { c.lastRead = 0; c.readProgress = 0; });
                }
                loadStats();
                updateProgressButton();
                showConfirm(icon('checkCircle'), `已重置 ${res.reset} 本漫画的阅读进度`, '', '好的', () => {});
            } else {
                showConfirm(icon('xCircle'), '重置失败', res.error || '未知错误', '关闭', () => {});
            }
        } catch (e) {
            showConfirm(icon('xCircle'), '重置失败', e.message, '关闭', () => {});
        }
    });
}

// ==================== 回收站 ====================

function getDelComics() {
    let delList = [];
    for (let i = 0; i < state.paths.length; i++) {
        (state.comics[i] || []).forEach((c, ci) => {
            if ((c.tags || []).includes('del')) {
                delList.push({ pIdx: i, cIdx: ci, name: c.name, path: state.paths[i], cover: c.cover, pages: c.pages });
            }
        });
    }
    return delList;
}

function openRecycleBin() {
    if (state.isMobile) closeSidebar();
    $('recycleOverlay').classList.remove('hidden');
    requestAnimationFrame(() => { $('recycleOverlay').classList.add('visible'); $('recyclePanel').classList.add('animate-in'); });
    renderRecycleBin();
}

function closeRecycleBin() {
    $('recycleOverlay').classList.remove('visible');
    setTimeout(() => { $('recycleOverlay').classList.add('hidden'); $('recyclePanel').classList.remove('animate-in'); }, 300);
}

function renderRecycleBin() {
    const list = getDelComics();
    const body = $('recycleBody');
    if (!list.length) {
        body.innerHTML = '<div class="recycle-empty">回收站是空的</div>';
        return;
    }
    let html = `<div class="recycle-count">共 ${list.length} 本待删除</div>`;
    html += '<div class="recycle-grid">';
    list.forEach(item => {
        const coverUrl = item.cover ? `/api/image?${new URLSearchParams({ path: item.path, comic: item.name, file: item.cover }).toString()}` : '';
        html += `<div class="recycle-card" data-pidx="${item.pIdx}" data-cidx="${item.cIdx}" data-name="${esc(item.name)}">
            ${coverUrl ? `<img class="recycle-thumb" src="${coverUrl}" loading="lazy" decoding="async">` : `<div class="recycle-thumb-placeholder">${icon('bookOpen')}</div>`}
            <div class="recycle-card-name">${esc(item.name)}</div>
            <div class="recycle-card-pages">${item.pages || '?'}P</div>
        </div>`;
    });
    html += '</div>';
    body.innerHTML = html;
}

function showRecycleMenu(e, card) {
    e.preventDefault();
    e.stopPropagation();
    const pIdx = parseInt(card.dataset.pidx);
    const cIdx = parseInt(card.dataset.cidx);
    const name = card.dataset.name;
    const comic = (state.comics[pIdx] || [])[cIdx];
    if (!comic) return;
    state.recycleTarget = { pIdx, cIdx, name };
    const menu = $('recycleMenu');
    $('recycleInfo').innerText = name;
    menu.classList.remove('hidden');
    let x = e.clientX || 0, y = e.clientY || 0;
    if (e.touches && e.touches.length) { x = e.touches[0].clientX; y = e.touches[0].clientY; }
    requestAnimationFrame(() => {
        const rect = menu.getBoundingClientRect();
        if (x + rect.width > window.innerWidth) x = window.innerWidth - rect.width - 8;
        if (y + rect.height > window.innerHeight) y = window.innerHeight - rect.height - 8;
        menu.style.left = Math.max(0, x) + 'px'; menu.style.top = Math.max(0, y) + 'px';
    });
}

async function recycleRestore() {
    if (!state.recycleTarget) return;
    const { pIdx, name } = state.recycleTarget;
    const comic = (state.comics[pIdx] || []).find(c => c.name === name);
    if (!comic) return;
    const newTags = (comic.tags || []).filter(t => t !== 'del');
    const res = await apiPost('meta', { path: state.paths[pIdx], name, tags: newTags });
    if (res.ok) {
        comic.tags = res.tags;
        applyFilters(false);
        loadStats();
        renderRecycleBin();
    }
    $('recycleMenu').classList.add('hidden');
    state.recycleTarget = null;
}

async function recycleDelete() {
    if (!state.recycleTarget) return;
    const { pIdx, name } = state.recycleTarget;
    const comic = (state.comics[pIdx] || []).find(c => c.name === name);
    if (!comic) return;
    $('recycleMenu').classList.add('hidden');
    showConfirm(icon('trash'), `彻底删除「${name}」？`, '文件将被永久删除，无法恢复。', '确认删除', async () => {
        try {
            const res = await apiPost('delete-comic', { path: state.paths[pIdx], name });
            if (res.ok) {
                const list = state.comics[pIdx] || [];
                const idx = list.findIndex(c => c.name === name);
                if (idx >= 0) list.splice(idx, 1);
                state.random.allComics = [];
                applyFilters(false);
                loadStats();
                renderRecycleBin();
            } else {
                showConfirm(icon('xCircle'), '删除失败', res.error || '未知错误', '关闭', () => {});
            }
        } catch (e) {
            showConfirm(icon('xCircle'), '删除失败', e.message, '关闭', () => {});
        }
    });
    state.recycleTarget = null;
}

function cleanAllRecycle() {
    const list = getDelComics();
    if (!list.length) return;
    showConfirm(icon('trash'), `清理全部 ${list.length} 本待删除漫画？`, '文件将被永久删除，无法恢复。', `确认删除 ${list.length} 本`, async () => {
        const res = await apiPost('clean-del', {});
        if (res.ok) {
            list.forEach(item => {
                const comics = state.comics[item.pIdx] || [];
                const idx = comics.findIndex(c => c.name === item.name);
                if (idx >= 0) comics.splice(idx, 1);
            });
            state.random.allComics = [];
            applyFilters(false);
            loadStats();
            renderRecycleBin();
            showConfirm(icon('checkCircle'), `已删除 ${res.deleted} 本漫画`, '文件已永久删除', '好的', () => {});
        } else {
            showConfirm(icon('xCircle'), '清理失败', res.error || '未知错误', '关闭', () => {});
        }
    });
}

// 回收站事件委托
(function setupRecycleDelegation() {
    const body = document.getElementById('recycleBody');
    if (!body) return;
    body.addEventListener('contextmenu', e => {
        const card = e.target.closest('.recycle-card');
        if (card) showRecycleMenu(e, card);
    });
    body.addEventListener('click', e => {
        const card = e.target.closest('.recycle-card');
        if (!card) return;
        // 移动端长按弹出菜单，桌面端右键弹出
        if (!state.isMobile) {
            // 桌面端单击也可以恢复
            const pIdx = parseInt(card.dataset.pidx);
            const name = card.dataset.name;
            const comic = (state.comics[pIdx] || []).find(c => c.name === name);
            if (comic) {
                state.recycleTarget = { pIdx, name };
                recycleRestore();
            }
        }
    });
    // 移动端长按
    let lt = null;
    body.addEventListener('touchstart', e => {
        const card = e.target.closest('.recycle-card');
        if (!card) return;
        lt = setTimeout(() => showRecycleMenu(e, card), 500);
    }, { passive: true });
    body.addEventListener('touchend', () => clearTimeout(lt));
    body.addEventListener('touchmove', () => clearTimeout(lt));
})();

function toggleTagFilter(tag) {
    if (state.activeTags.has(tag)) {
        state.activeTags.delete(tag);
    } else {
        state.activeTags.add(tag);
    }
    state.activeTag = state.activeTags.size === 1 ? [...state.activeTags][0] : '';
    renderHomepageTags();
    renderActiveTags();
    updateTagPickerBtn();
    invalidateCache();
    applyFilters();
}

function togglePathFilter(idx) {
    if (state.activePaths.has(idx)) {
        state.activePaths.delete(idx);
    } else {
        state.activePaths.add(idx);
    }
    renderNavLinks();
    invalidateCache();
    applyFilters();
}

function clearPathFilter() {
    state.activePaths.clear();
    renderNavLinks();
    invalidateCache();
    applyFilters();
}

function renderNavLinks() {
    const libHtml = state.paths.map((p, i) => {
        const count = (state.comics[i] || []).length;
        const active = state.activePaths.has(i);
        return `<div class="nav-item ${active ? 'active' : ''}" data-action="togglePathFilter" data-arg1="${i}">
            <span class="nav-item-icon">${icon('folder')}</span>
            <span class="nav-item-name">${esc(p.split(/[\\\/]/).pop())}</span>
            <span class="nav-item-count">${count}</span>
        </div>`;
    }).join('');
    const allActive = state.activePaths.size === 0;
    $('navLinks').innerHTML = `<div class="nav-item nav-item-all ${allActive ? 'active' : ''}" data-action="clearPathFilter" data-tip="全部书库"><span class="nav-item-icon">${icon('book')}</span><span class="nav-item-name">全部书库</span></div>` + libHtml;
}

// ==================== 过滤与排序（合并所有书库） ====================

function invalidateCache() {
    state.allFiltered = [];
}

function getAllFiltered() {
    if (state.allFiltered.length) return state.allFiltered;

    let all = [];
    for (let i = 0; i < state.paths.length; i++) {
        // 书库筛选：有选中书库时只显示选中的
        if (state.activePaths.size > 0 && !state.activePaths.has(i)) continue;
        const list = state.comics[i] || [];
        for (let ci = 0; ci < list.length; ci++) {
            const c = list[ci];
            c._pIdx = i;
            c._cIdx = ci;
            all.push(c);
        }
    }

    const showingDel = state.activeTags.has('del') || state.filterText === 'del';
    if (!showingDel) all = all.filter(c => !(c.tags || []).includes('del'));
    // 多选 tag：必须同时拥有所有选中的 tag（AND 逻辑）
    if (state.activeTags.size > 0) {
        const realTags = [...state.activeTags].filter(t => t !== FAV_TAG);
        const wantFav = state.activeTags.has(FAV_TAG);
        if (wantFav) all = all.filter(c => c.fav);
        if (realTags.length) all = all.filter(c => realTags.every(t => (c.tags || []).includes(t)));
    }
    if (state.filterText) {
        all = all.filter(c =>
            c.name.toLowerCase().includes(state.filterText) ||
            (c.tags || []).some(t => t.toLowerCase().includes(state.filterText))
        );
    }

    const [key, dir] = SORT_MODES[state.sortMode].split('-');
    all.sort((a, b) => {
        const va = key === 'lastRead' ? (a.lastRead || 0) : a.mtime;
        const vb = key === 'lastRead' ? (b.lastRead || 0) : b.mtime;
        return dir === 'desc' ? vb - va : va - vb;
    });
    // 最近阅读模式：只保留有阅读记录的，最多 30 本
    if (key === 'lastRead') {
        all = all.filter(c => c.lastRead).slice(0, 30);
    }

    state.allFiltered = all;
    return all;
}

function hideShelfEmpty() {
    const el = $('shelfEmpty');
    if (el) el.classList.add('hidden');
}

/**
 * 书架上一本书都没有时给出可操作的提示，而不是留一片空白。
 * failedPaths: 加载失败的书库 [{path, reason}]
 */
function renderShelfEmpty(failedPaths) {
    const el = $('shelfEmpty');
    if (!el) return;
    const total = Object.values(state.comics).reduce((n, list) => n + (list ? list.length : 0), 0);
    if (total > 0) { hideShelfEmpty(); return; }

    let html;
    if (!state.paths.length) {
        html = `<div class="shelf-empty-title">还没有配置漫画目录</div>
            <div>编辑 <code>config.json</code>，把漫画根目录填进 <code>libraryPaths</code>，然后重启程序。</div>`;
    } else if (failedPaths && failedPaths.length) {
        html = `<div class="shelf-empty-title">书库目录读不到</div>
            <div>下面这些目录不存在或没有权限，检查路径，或编辑 <code>config.json</code> 后重启：</div>
            <ul class="shelf-empty-list">${failedPaths.map(f => `<li>${esc(f.path)} —— ${esc(f.reason)}</li>`).join('')}</ul>`;
    } else {
        html = `<div class="shelf-empty-title">书库还是空的</div>
            <div>把漫画文件夹放进 <code>${esc(state.paths[0])}</code>，每个子文件夹会被当成一本漫画。</div>
            <div class="shelf-empty-hint">想换个目录？编辑 <code>config.json</code> 的 <code>libraryPaths</code> 后重启程序。</div>`;
    }
    el.innerHTML = html;
    el.classList.remove('hidden');
}

function applyFilters(scrollToStart) {
    state.allFiltered = [];
    state.displayedCount = 0;
    state.currentPage = 0;
    const grid = $('grid-all');
    grid.innerHTML = '';
    loadMoreItems();
    refreshPager();
    if (state.allFiltered.length) hideShelfEmpty();
    if (scrollToStart !== false) scrollToTop();
}

// ==================== 无限滚动 + 页码追踪 ====================

function setupInfiniteScroll() {
    const sentinel = $('loadMoreSentinel');
    const observer = new IntersectionObserver((entries) => {
        if (entries[0].isIntersecting && !state.isLoadingMore) {
            loadMoreItems();
        }
    }, {
        root: $('bookshelf'),
        rootMargin: '200px 0px'
    });
    observer.observe(sentinel);

    // 滚动时更新页码 + 隐藏预览（节流）
    let _pagerTimer = null;
    $('bookshelf').addEventListener('scroll', () => {
        clearTimeout(_pagerTimer);
        _pagerTimer = setTimeout(() => { refreshPager(); hidePreview(); }, 100);
    }, { passive: true });
}

function loadMoreItems() {
    if (state.isLoadingMore) return;
    const all = getAllFiltered();
    if (state.displayedCount >= all.length) return;

    state.isLoadingMore = true;
    const start = state.displayedCount;
    const end = Math.min(start + PER_PAGE, all.length);
    const pageItems = all.slice(start, end);

    renderMoreItems(pageItems, start);
    state.displayedCount = end;
    state.isLoadingMore = false;
}

// ==================== 页码控件 ====================

function refreshPager() {
    const all = getAllFiltered();
    const totalCount = all.length;
    const topPager = $('topPager');
    if (!topPager) return;

    if (!totalCount) { topPager.style.display = 'none'; return; }
    topPager.style.display = '';

    const totalPages = Math.ceil(totalCount / PER_PAGE);

    // 根据滚动位置计算当前页：找到第一个可见卡片的索引
    const grid = $('grid-all');
    const cards = grid.children;
    const bookshelf = $('bookshelf');
    const scrollTop = bookshelf.scrollTop;
    const viewTop = scrollTop + 80; // 稍微偏移，让页码更早切换
    let firstVisibleIdx = 0;
    for (let i = 0; i < cards.length; i++) {
        if (cards[i].offsetTop + cards[i].offsetHeight > viewTop) {
            firstVisibleIdx = i;
            break;
        }
    }
    const currentPage = Math.floor(firstVisibleIdx / PER_PAGE);
    state.currentPage = currentPage;

    // 生成页码按钮
    let pageButtons = '';
    const maxShow = 7;
    let startP = 0, endP = totalPages;
    if (totalPages > maxShow) {
        startP = Math.max(0, currentPage - 2);
        endP = Math.min(totalPages, startP + maxShow);
        if (endP - startP < maxShow) startP = Math.max(0, endP - maxShow);
    }

    if (startP > 0) {
        pageButtons += `<button class="top-pager-btn" data-action="goToPage" data-arg1="0">1</button>`;
        if (startP > 1) pageButtons += `<span class="top-pager-ellipsis">…</span>`;
    }
    for (let p = startP; p < endP; p++) {
        pageButtons += `<button class="top-pager-btn ${p === currentPage ? 'active' : ''}" data-action="goToPage" data-arg1="${p}">${p + 1}</button>`;
    }
    if (endP < totalPages) {
        if (endP < totalPages - 1) pageButtons += `<span class="top-pager-ellipsis">…</span>`;
        pageButtons += `<button class="top-pager-btn" data-action="goToPage" data-arg1="${totalPages - 1}">${totalPages}</button>`;
    }

    const from = firstVisibleIdx + 1;
    const to = Math.min(firstVisibleIdx + PER_PAGE, totalCount);

    topPager.innerHTML = `
        <button class="top-pager-btn nav" ${currentPage === 0 ? 'disabled' : ''} data-action="goToPage" data-arg1="${currentPage - 1}">‹</button>
        ${pageButtons}
        <button class="top-pager-btn nav" ${currentPage >= totalPages - 1 ? 'disabled' : ''} data-action="goToPage" data-arg1="${currentPage + 1}">›</button>
        <span class="top-pager-info">${from}-${to} / ${totalCount}</span>
    `;
}

function goToPage(page) {
    const all = getAllFiltered();
    const totalPages = Math.ceil(all.length / PER_PAGE);
    page = Math.max(0, Math.min(page, totalPages - 1));

    const targetIdx = page * PER_PAGE; // 该页第一项在 all 数组中的索引
    const grid = $('grid-all');

    // 如果目标页尚未渲染，先加载到那一页
    if (targetIdx >= state.displayedCount) {
        renderMoreItems(all.slice(state.displayedCount, targetIdx + PER_PAGE), state.displayedCount);
        state.displayedCount = targetIdx + PER_PAGE;
    }

    // 滚动到目标卡片位置
    const card = grid.children[targetIdx];
    if (card) {
        card.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    // 立即更新页码高亮
    state.currentPage = page;
    refreshPager();
}

// ==================== 书架渲染（追加模式） ====================

function renderMoreItems(pageItems, startIdx) {
    const grid = $('grid-all');
    const frag = document.createDocumentFragment();

    pageItems.forEach((c, idx) => {
        const path = state.paths[c._pIdx];
        const tags = (c.tags || []).slice(0, 2).map(t => `<span class="card-tag">${esc(t)}</span>`).join('');
        const isDel = (c.tags || []).includes('del');
        const card = document.createElement('div');
        card.className = `comic-card ${c.fav ? 'is-fav' : ''} ${isDel ? 'is-del' : ''}`;
        card.dataset.pidx = c._pIdx;
        card.dataset.cidx = c._cIdx;
        card.dataset.name = c.name;
        card.dataset.path = encodeURIComponent(path);
        card.dataset.comic = encodeURIComponent(c.name);
        card.dataset.cover = c.cover || '';
        card.innerHTML = `
            <img class="lazy-thumb" loading="lazy" decoding="async">
            ${c.fav ? `<div class="fav-badge">${icon('star')}</div>` : ''}
            ${c.pages ? `<span class="page-count">${c.pages}P</span>` : ''}
            <button class="card-action-btn">${icon('moreH')}</button>
            <div class="info">${esc(c.name)}</div>
            ${tags ? `<div class="card-tags">${tags}</div>` : ''}
        `;
        frag.appendChild(card);
    });

    grid.appendChild(frag);
    observeNewCovers(grid);
}

// ==================== 卡片交互 ====================

function handleCardClick(e, pIdx, cIdx) {
    hidePreview();
    $('contextMenu').classList.add('hidden');
    const comic = (state.comics[pIdx] || [])[cIdx];
    if (!comic) return;
    openComic(pIdx, cIdx);
}

function handleCardContext(e, pIdx, name) {
    e.preventDefault && e.preventDefault();
    e.stopPropagation && e.stopPropagation();
    hidePreview();
    showCtxMenu(e, pIdx, name);
}

// ==================== 悬浮预览 ====================

function showPreview(card, e) {
    const img = card.querySelector('img');
    if (!img || !img.src || !img.classList.contains('loaded')) return;

    let preview = document.querySelector('.hover-preview');
    if (!preview) { preview = document.createElement('div'); preview.className = 'hover-preview'; document.body.appendChild(preview); }
    preview.innerHTML = `<img src="${img.src}" alt="${esc(card.dataset.name)}"><div class="preview-title">${esc(card.dataset.name)}</div>`;
    preview.classList.add('show');

    // 定位：优先放卡片右侧，放不下就放左侧，再不行居中
    const rect = card.getBoundingClientRect();
    const pw = 360, ph = Math.min(preview.offsetHeight || 480, window.innerHeight * 0.85);
    const gap = 12;
    let x, y;

    // 水平：右侧优先
    if (rect.right + gap + pw <= window.innerWidth) {
        x = rect.right + gap;
    } else if (rect.left - gap - pw >= 0) {
        x = rect.left - gap - pw;
    } else {
        x = Math.max(8, (window.innerWidth - pw) / 2);
    }

    // 垂直：与卡片顶部对齐，保证不超出屏幕
    y = rect.top;
    if (y + ph > window.innerHeight - 8) y = window.innerHeight - ph - 8;
    if (y < 8) y = 8;

    preview.style.left = x + 'px';
    preview.style.top = y + 'px';
}

function hidePreview() {
    const p = document.querySelector('.hover-preview');
    if (p && p.classList.contains('show')) p.classList.remove('show');
}

function toggleHoverPreview() {
    state.hoverPreview = !state.hoverPreview;
    localStorage.setItem('hoverPreviewOff', state.hoverPreview ? '0' : '1');
    if (!state.hoverPreview) hidePreview();
    updateHoverPreviewBtn();
    showToast(state.hoverPreview ? '悬浮预览已开启' : '悬浮预览已关闭', 'success');
}

function updateHoverPreviewBtn() {
    const btn = $('btnHoverPreview');
    if (!btn) return;
    btn.style.opacity = state.hoverPreview ? '' : '0.5';
    const c = $('hoverPreviewState');
    if (c) c.textContent = state.hoverPreview ? '开' : '关';
}

// ==================== 封面加载 ====================

function coverUrl(p, c, coverFile) {
    return `/api/image?${new URLSearchParams({ path: p, comic: c, file: coverFile }).toString()}`;
}

function initCoverSystem() {
    document.querySelectorAll('.lazy-thumb:not(.loaded)').forEach(img => observeSingleCover(img));
}

function observeNewCovers(grid) {
    grid.querySelectorAll('.lazy-thumb:not(.loaded)').forEach(img => observeSingleCover(img));
}

function observeSingleCover(img) {
    if (img.classList.contains('loaded') || img.dataset.cmObserved) return;
    img.dataset.cmObserved = 'true';

    const card = img.closest('.comic-card');
    if (!card) return;

    const p = decodeURIComponent(card.dataset.path || '');
    const c = decodeURIComponent(card.dataset.comic || '');
    const coverFile = card.dataset.cover || '';
    const cacheKey = `${p}|${c}`;

    // 直接用 /api/comics 已返回的 cover 字段，省掉 pages API 调用
    if (coverFile) {
        CoverManager.observe(img, coverUrl(p, c, coverFile), cacheKey);
    } else {
        // 没有封面数据时才降级调 pages API
        api('pages', { path: p, comic: c }).then(data => {
            if (data.pages?.length) {
                CoverManager.observe(img, coverUrl(p, c, data.pages[0]), cacheKey);
            } else {
                CoverManager.observe(img, '', cacheKey);
            }
        }).catch(() => {
            img.src = 'data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIxMDAlIiBoZWlnaHQ9IjEwMCUiPjxyZWN0IHdpZHRoPSIxMDAlIiBoZWlnaHQ9IjEwMCUiIGZpbGw9IiNlZWVlZWUiLz48dGV4dCB4PSI1MCUiIHk9IjUwJSIgZmlsbD0iIzY2NiIgZm9udC1zaXplPSIxNHB4IiB0ZXh0LWFuY2hvcj0ibWlkZGxlIiBkeT0iLjNlbSI+TG9hZCBGYWlsZWQ8L3RleHQ+PC9zdmc+';
            img.classList.add('error');
        });
    }
}

// ==================== 右键菜单 ====================

function showCtxMenu(e, pIdx, name) {
    state.ctxTarget = { pIdx, name };
    const comic = (state.comics[pIdx] || []).find(c => c.name === name);
    if (!comic) return;

    const menu = $('contextMenu');
    $('ctxInfo').innerText = `${name} · ${comic.pages || '?'}P`;
    menu.classList.remove('hidden');

    let x = e.clientX || e.pageX || 0, y = e.clientY || e.pageY || 0;
    if (e.touches && e.touches.length) { x = e.touches[0].clientX; y = e.touches[0].clientY; }

    requestAnimationFrame(() => {
        const rect = menu.getBoundingClientRect();
        if (x + rect.width > window.innerWidth) x = window.innerWidth - rect.width - 8;
        if (y + rect.height > window.innerHeight) y = window.innerHeight - rect.height - 8;
        menu.style.left = Math.max(0, x) + 'px'; menu.style.top = Math.max(0, y) + 'px';
    });
}

function ctxOpenDetail() {
    if (!state.ctxTarget) return;
    $('contextMenu').classList.add('hidden');
    openDetailPanel(state.ctxTarget.pIdx, state.ctxTarget.name);
}

async function ctxToggleDel() {
    if (!state.ctxTarget) return;
    $('contextMenu').classList.add('hidden');
    const { pIdx, name } = state.ctxTarget;
    const comic = (state.comics[pIdx] || []).find(c => c.name === name);
    if (!comic) return;
    const tags = comic.tags || [];
    const hasDel = tags.includes('del');
    const newTags = hasDel ? tags.filter(t => t !== 'del') : [...tags, 'del'];
    const res = await apiPost('meta', { path: state.paths[pIdx], name, tags: newTags });
    if (res.ok) {
        comic.tags = res.tags;
        const card = document.querySelector(`.comic-card[data-name="${CSS.escape(name)}"]`);
        if (card) card.classList.toggle('is-del', res.tags.includes('del'));
        showToast(hasDel ? '已取消待删除' : '已标记待删除', 'success');
        loadStats();
    }
}

// ==================== 漫画详情面板 ====================

function openDetailPanel(pIdx, name) {
    const comic = (state.comics[pIdx] || []).find(c => c.name === name);
    if (!comic) return;
    state.ctxTarget = { pIdx, name };

    // 封面
    const path = state.paths[pIdx];
    const coverImg = $('detailCover');
    if (comic.cover) {
        coverImg.src = coverUrl(path, name, comic.cover);
        coverImg.style.display = '';
    } else {
        coverImg.src = '';
        coverImg.style.display = 'none';
    }

    // 标题 + 副标题
    $('detailTitle').innerText = name;
    const libName = path.split('\\').pop() || path;
    const dateStr = comic.mtime ? new Date(comic.mtime * 1000).toLocaleDateString('zh-CN') : '';
    let subtitle = `${comic.pages || '?'}P · ${libName}`;
    if (dateStr) subtitle += ` · ${dateStr}`;
    if (comic.jmId) subtitle += ` · JM${comic.jmId}`;
    $('detailSubtitle').innerText = subtitle;

    // 快捷操作按钮状态
    renderDetailActions(comic);

    // 阅读信息
    renderDetailInfo(comic);

    // 标签区域
    renderTagList(comic.tags || []);
    renderTagSuggestions(comic.tags || []);
    $('tagInput').value = '';

    $('tagOverlay').classList.remove('hidden');
    requestAnimationFrame(() => { $('tagOverlay').classList.add('visible'); $('tagModal').classList.add('animate-in'); });
}

function renderDetailActions(comic) {
    $('detailFavBtn').innerHTML = comic.fav ? `${icon('heart')} 已收藏` : `${icon('star')} 收藏`;
    $('detailFavBtn').classList.toggle('active', !!comic.fav);
    const isDel = (comic.tags || []).includes('del');
    $('detailDelBtn').innerHTML = isDel ? `${icon('refresh')} 取消待删除` : `${icon('trash')} 待删除`;
    $('detailDelBtn').classList.toggle('active', isDel);
}

function renderDetailInfo(comic) {
    let html = '';
    // 阅读进度
    if (comic.readProgress > 0 && comic.pages > 0) {
        const pct = Math.min(100, Math.round(comic.readProgress / comic.pages * 100));
        html += `<div class="detail-info-row">
            <span class="detail-info-label">阅读进度</span>
            <div class="detail-progress-bar"><div class="detail-progress-fill" style="width:${pct}%"></div></div>
            <span class="detail-info-value">第${comic.readProgress}页 / 共${comic.pages}页</span>
        </div>`;
    }
    // 上次阅读
    if (comic.lastRead) {
        const t = relTime(comic.lastRead) || new Date(comic.lastRead * 1000).toLocaleString('zh-CN');
        html += `<div class="detail-info-row"><span class="detail-info-label">上次阅读</span><span class="detail-info-value">${esc(t)}</span></div>`;
    }
    if (!html) {
        html = '<div class="detail-info-row"><span class="detail-info-label">暂无阅读记录</span></div>';
    }
    $('detailInfo').innerHTML = html;
}

async function detailToggleFav() {
    if (!state.ctxTarget) return;
    const { pIdx, name } = state.ctxTarget;
    const comic = (state.comics[pIdx] || []).find(c => c.name === name);
    if (!comic) return;
    const res = await apiPost('meta', { path: state.paths[pIdx], name, fav: !comic.fav });
    if (res.ok) {
        comic.fav = res.fav;
        const card = document.querySelector(`.comic-card[data-name="${CSS.escape(name)}"]`);
        if (card) card.classList.toggle('is-fav', res.fav);
        renderDetailActions(comic);
        loadStats();
    }
}

async function detailToggleDel() {
    if (!state.ctxTarget) return;
    const { pIdx, name } = state.ctxTarget;
    const comic = (state.comics[pIdx] || []).find(c => c.name === name);
    if (!comic) return;
    const tags = comic.tags || [];
    const hasDel = tags.includes('del');
    const newTags = hasDel ? tags.filter(t => t !== 'del') : [...tags, 'del'];
    const res = await apiPost('meta', { path: state.paths[pIdx], name, tags: newTags });
    if (res.ok) {
        comic.tags = res.tags;
        const card = document.querySelector(`.comic-card[data-name="${CSS.escape(name)}"]`);
        if (card) card.classList.toggle('is-del', res.tags.includes('del'));
        renderDetailActions(comic);
        renderTagList(res.tags);
        renderTagSuggestions(res.tags);
        loadStats();
    }
}

async function detailResetProgress() {
    if (!state.ctxTarget) return;
    const { pIdx, name } = state.ctxTarget;
    const comic = (state.comics[pIdx] || []).find(c => c.name === name);
    if (!comic) return;
    const res = await apiPost('meta', { path: state.paths[pIdx], name, lastRead: 0, readProgress: 0 });
    if (res.ok) {
        comic.lastRead = 0;
        comic.readProgress = 0;
        renderDetailInfo(comic);
        toast('阅读进度已重置');
    }
}

async function detailDeleteComic() {
    if (!state.ctxTarget) return;
    const { pIdx, name } = state.ctxTarget;
    closeTagModal();
    showConfirm(icon('trash'), `删除「${name}」？`, '文件将被永久删除，无法恢复。', '确认删除', async () => {
        try {
            const res = await apiPost('delete-comic', { path: state.paths[pIdx], name });
            if (res.ok) {
                const arr = state.comics[pIdx];
                if (arr) {
                    const idx = arr.findIndex(c => c.name === name);
                    if (idx >= 0) arr.splice(idx, 1);
                }
                invalidateCache();
                applyFilters();
                loadStats();
                showToast('已删除', 'success');
            } else {
                showConfirm(icon('xCircle'), '删除失败', res.error || '未知错误', '关闭', () => {});
            }
        } catch (e) {
            showConfirm(icon('xCircle'), '删除失败', e.message, '关闭', () => {});
        }
    });
}

function closeTagModal() {
    $('tagOverlay').classList.remove('visible');
    setTimeout(() => { $('tagOverlay').classList.add('hidden'); $('tagModal').classList.remove('animate-in'); }, 300);
}

function renderTagList(tags) {
    $('tagList').innerHTML = tags.map(t =>
        `<span class="tag-chip">${esc(t)} <button class="tag-remove" data-action="removeTag" data-arg1="${esc(t)}">${icon('x')}</button></span>`
    ).join('') || '<span class="tag-empty">暂无标签</span>';
}

function renderTagSuggestions(currentTags) {
    // 推荐所有已有标签（排除 del、收藏），按分类分组显示
    const suggestions = Object.entries(state.allTags)
        .filter(([t]) => !currentTags.includes(t) && t !== 'del' && t !== FAV_TAG)
        .sort((a, b) => b[1] - a[1]);
    if (!suggestions.length) { $('tagSuggestions').innerHTML = ''; return; }

    const cats = state.tagCategories || {};
    const tagToCat = {};
    for (const [cat, tags] of Object.entries(cats)) {
        for (const t of tags) tagToCat[t] = cat;
    }

    const grouped = {};
    const uncategorized = [];
    for (const [t, count] of suggestions) {
        const cat = tagToCat[t];
        if (cat) {
            if (!grouped[cat]) grouped[cat] = [];
            grouped[cat].push({ tag: t, count });
        } else {
            uncategorized.push({ tag: t, count });
        }
    }

    let html = '';

    for (const [cat, items] of Object.entries(grouped)) {
        html += `<div class="tag-suggest-label">${esc(cat)}</div>`;
        html += items.map(i => {
            const onHomepage = state.homepageTags.has(i.tag);
            return `<button class="tag-suggest-pill ${onHomepage ? 'tag-suggest-visible' : ''}" data-action="addTagDirect" data-arg1="${esc(i.tag)}">${esc(i.tag)}<span class="tag-suggest-count">${i.count}</span></button>`;
        }).join('');
    }
    if (uncategorized.length) {
        html += `<div class="tag-suggest-label">其他</div>`;
        html += uncategorized.map(i => {
            const onHomepage = state.homepageTags.has(i.tag);
            return `<button class="tag-suggest-pill ${onHomepage ? 'tag-suggest-visible' : ''}" data-action="addTagDirect" data-arg1="${esc(i.tag)}">${esc(i.tag)}<span class="tag-suggest-count">${i.count}</span></button>`;
        }).join('');
    }
    $('tagSuggestions').innerHTML = html;
}

async function addTag() {
    const val = $('tagInput').value.trim();
    if (!val || !state.ctxTarget) return;
    // 如果是新标签，先选分类
    if (!state.allTags[val]) {
        showCategoryPicker(val);
        return;
    }
    await addTagDirect(val);
    $('tagInput').value = '';
}

function showCategoryPicker(tag) {
    const cats = Object.keys(state.tagCategories || {});
    if (!cats.length) {
        // 没有分类，直接创建
        addNewTagWithCat(tag, '');
        return;
    }
    const suggest = $('tagSuggestions');
    let html = `<div class="tag-suggest-label">新标签「<b>${esc(tag)}</b>」选择分类：</div>`;
    html += cats.map(c => `<button class="tag-suggest-pill" data-action="addNewTagWithCat" data-arg1="${esc(tag)}" data-arg2="${esc(c)}">${esc(c)}</button>`).join('');
    html += `<button class="tag-suggest-pill tag-suggest-skip" data-action="addNewTagWithCat" data-arg1="${esc(tag)}" data-arg2="">跳过</button>`;
    suggest.innerHTML = html;
}

async function addNewTagWithCat(tag, cat) {
    if (cat) {
        if (!state.tagCategories[cat]) state.tagCategories[cat] = [];
        if (!state.tagCategories[cat].includes(tag)) state.tagCategories[cat].push(tag);
        saveTagConfig();
    }
    // 新标签默认不在主页显示（不加入 homepageTags）
    await addTagDirect(tag);
    $('tagInput').value = '';
}

async function addTagDirect(tag) {
    if (!state.ctxTarget) return;
    const { pIdx, name } = state.ctxTarget;
    const comic = (state.comics[pIdx] || []).find(c => c.name === name);
    if (!comic) return;
    const res = await apiPost('meta', { path: state.paths[pIdx], name, addTag: tag });
    if (res.ok) {
        comic.tags = res.tags;
        // 新标签默认不在主页显示（不加入 homepageTags 白名单）
        await loadStats();
        renderTagList(res.tags);
        renderTagSuggestions(res.tags);
        saveTagConfig();
    }
}

async function removeTag(tag) {
    if (!state.ctxTarget) return;
    const { pIdx, name } = state.ctxTarget;
    const comic = (state.comics[pIdx] || []).find(c => c.name === name);
    if (!comic) return;
    const res = await apiPost('meta', { path: state.paths[pIdx], name, removeTag: tag });
    if (res.ok) {
        comic.tags = res.tags;
        await loadStats();
        renderTagList(res.tags);
        renderTagSuggestions(res.tags);
    }
}

// ==================== 标签管理器 ====================

async function openTagManager() {
    if (state.isMobile) closeSidebar();
    $('tagMgrOverlay').classList.remove('hidden');
    $('tagMgrSearch').value = '';
    requestAnimationFrame(() => {
        $('tagMgrOverlay').classList.add('visible');
        $('tagMgrModal').classList.add('animate-in');
    });
    renderTagManager();
    // 保存标签分类是整体覆盖，而本地这份只在打开页面时读过一次。
    // 下载时后端会自动把作者补进「艺术家」分类，这里先拉一次最新的，
    // 否则接着点保存就会用旧副本把后端刚写的作者覆盖掉。
    await loadTagConfig();
    renderTagManager();
    renderTagBar();
}

function closeTagManager() {
    $('tagMgrOverlay').classList.remove('visible');
    setTimeout(() => {
        $('tagMgrOverlay').classList.add('hidden');
        $('tagMgrModal').classList.remove('animate-in');
    }, 300);
}

function renderTagManager() {
    const raw = ($('tagMgrSearch').value || '').trim().toLowerCase();
    const keywords = raw.split(/[\s,，]+/).filter(Boolean);
    const allTags = Object.entries(state.allTags).sort((a, b) => b[1] - a[1]);
    const cats = state.tagCategories || {};

    const tagToCat = {};
    for (const [cat, tags] of Object.entries(cats)) {
        for (const t of tags) tagToCat[t] = cat;
    }

    const matchTag = (t) => {
        if (!keywords.length) return true;
        const lower = t.toLowerCase();
        return keywords.every(k => lower.includes(k));
    };

    const grouped = {};
    const uncategorized = [];
    for (const [t, count] of allTags) {
        if (t === 'del' || t === FAV_TAG) continue;
        if (!matchTag(t)) continue;
        const cat = tagToCat[t];
        if (cat) {
            if (!grouped[cat]) grouped[cat] = [];
            grouped[cat].push({ tag: t, count });
        } else {
            uncategorized.push({ tag: t, count });
        }
    }

    const catNames = Object.keys(grouped);
    const totalMatched = Object.values(grouped).flat().length + uncategorized.length;
    const totalTags = Object.keys(state.allTags).filter(t => t !== 'del').length;
    const visibleTags = Object.keys(state.allTags).filter(t => t !== 'del' && t !== FAV_TAG && state.homepageTags.has(t)).length + (state.allTags[FAV_TAG] ? 1 : 0);

    // ===== 侧边栏 =====
    let sidebarHtml = '';
    // 收藏
    const favCount = state.allTags[FAV_TAG] || 0;
    if (favCount > 0 && matchTag(FAV_TAG)) {
        sidebarHtml += `<div class="tagmgr-nav-item fav" data-action="tagMgrScrollTo" data-arg1="__fav__">${icon('star')} 收藏 <span class="tagmgr-nav-count">${favCount}</span></div>`;
    }
    sidebarHtml += `<div class="tagmgr-nav-item active" data-action="tagMgrScrollTo" data-arg1="all">${icon('list')} 全部 <span class="tagmgr-nav-count">${totalTags}</span></div>`;
    for (const c of catNames) {
        const count = grouped[c].length;
        sidebarHtml += `<div class="tagmgr-nav-item" data-action="tagMgrScrollTo" data-arg1="${esc(c)}">${icon('folder')} ${esc(c)} <span class="tagmgr-nav-count">${count}</span></div>`;
    }
    if (uncategorized.length) {
        sidebarHtml += `<div class="tagmgr-nav-item" data-action="tagMgrScrollTo" data-arg1="__uncat__">${icon('folder')} 未分类 <span class="tagmgr-nav-count">${uncategorized.length}</span></div>`;
    }
    // 新建分类
    sidebarHtml += `<div class="tagmgr-nav-new">
        <input type="text" class="tagmgr-nav-input" id="tagMgrNewCat" placeholder="新分类..." onkeydown="if(event.key==='Enter'){createTagCategory();renderTagManager()}">
        <button class="tagmgr-nav-add" data-action="createTagCategory" onclick="setTimeout(()=>renderTagManager(),50)">+</button>
    </div>`;
    $('tagMgrSidebar').innerHTML = sidebarHtml;

    // ===== 主区域 =====
    let mainHtml = '';
    // 统计条
    mainHtml += `<div class="tagmgr-summary">
        <span>共 <b>${totalTags}</b> 个标签</span>
        ${keywords.length ? `<span>· 匹配 <b>${totalMatched}</b></span>` : ''}
        <span>· 主页显示 <b>${visibleTags}</b></span>
    </div>
    <div class="tagmgr-hint">${icon('info')} 开关控制标签是否出现在主页顶部；新标签默认不在主页显示，需手动开启</div>`;

    if (totalMatched === 0) {
        mainHtml += '<div class="tagmgr-empty">没有匹配的标签</div>';
        $('tagMgrMain').innerHTML = mainHtml;
        return;
    }

    const renderCat = (catName, items, id, ic) => {
        if (!items.length) return '';
        let h = `<div class="tagmgr-cat" id="tm-${id}">
            <div class="tagmgr-cat-header">
                <span class="tagmgr-cat-name">${ic || icon('folder')} ${id === '__uncat__' ? '未分类' : esc(catName)}</span>
                <span class="tagmgr-cat-count">${items.length}</span>
                ${id !== '__uncat__' && id !== '__fav__' ? `<button class="tagmgr-cat-del" data-action="deleteTagCategory" data-arg1="${esc(catName)}" title="删除分类">${icon('x')}</button>` : ''}
            </div>
            <div class="tagmgr-cat-grid">${items.map(i => tagMgrItem(i.tag, i.count)).join('')}</div>
        </div>`;
        return h;
    };

    // 收藏
    if (favCount > 0 && matchTag(FAV_TAG)) {
        mainHtml += `<div class="tagmgr-cat" id="tm-__fav__">
            <div class="tagmgr-cat-header">
                <span class="tagmgr-cat-name">${icon('star')} 收藏</span>
                <span class="tagmgr-cat-count">${favCount}</span>
            </div>
            <div class="tagmgr-cat-grid">
                <div class="tagmgr-card fav-card">
                    <span class="tagmgr-card-name">${icon('star')} 收藏（系统标签）</span>
                    <span class="tagmgr-card-count">${favCount}</span>
                </div>
            </div>
        </div>`;
    }

    for (const [cat, items] of Object.entries(grouped)) {
        mainHtml += renderCat(cat, items, cat);
    }
    if (uncategorized.length) {
        mainHtml += renderCat('', uncategorized, '__uncat__', icon('folder'));
    }

    $('tagMgrMain').innerHTML = mainHtml;
}

function tagMgrScrollTo(id, btn) {
    // 更新侧边栏高亮
    document.querySelectorAll('.tagmgr-nav-item').forEach(b => b.classList.remove('active'));
    if (btn) btn.classList.add('active');
    if (id === 'all') {
        $('tagMgrMain').scrollTo({ top: 0, behavior: 'smooth' });
        return;
    }
    const el = document.getElementById('tm-' + id);
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start', inline: 'nearest' });
}

function tagMgrItem(tag, count) {
    const onHomepage = state.homepageTags.has(tag);
    const catOpts = Object.keys(state.tagCategories || {}).map(c =>
        `<option value="${esc(c)}">${esc(c)}</option>`
    ).join('');
    const currentCat = (function() {
        for (const [c, tags] of Object.entries(state.tagCategories || {})) {
            if (tags.includes(tag)) return c;
        }
        return '';
    })();
    return `<div class="tagmgr-card ${onHomepage ? '' : 'is-hidden'}" data-tag="${esc(tag)}">
        <div class="tagmgr-card-top">
            <button class="tagmgr-toggle ${onHomepage ? 'on' : ''}" data-action="toggleTagVisibility" data-arg1="${esc(tag)}" title="${onHomepage ? '从主页隐藏' : '在主页显示'}">
                <span class="tagmgr-toggle-dot"></span>
            </button>
            <span class="tagmgr-card-name" data-action="filterByTagFromMgr" data-arg1="${esc(tag)}">${esc(tag)}</span>
            <span class="tagmgr-card-count">${count}</span>
        </div>
        <div class="tagmgr-card-actions">
            <select class="tagmgr-card-cat" data-action="moveTagToCatSelect" data-arg1="${esc(tag)}">
                <option value="" ${!currentCat ? 'selected' : ''}>未分类</option>
                ${catOpts}
                <option value="__new__">+ 新建</option>
            </select>
            <button class="tagmgr-card-btn rename" data-action="renameTag" data-arg1="${esc(tag)}" title="重命名">${icon('edit')}</button>
            <button class="tagmgr-card-btn delete" data-action="deleteTag" data-arg1="${esc(tag)}" title="全局删除">${icon('trash')}</button>
        </div>
    </div>`;
}

function toggleTagVisibility(tag) {
    if (state.homepageTags.has(tag)) state.homepageTags.delete(tag);
    else state.homepageTags.add(tag);
    saveTagConfig();
    renderTagBar();
    renderTagManager();
}

function tagMgrShowAll() {
    // 把所有标签加入主页白名单
    for (const t of Object.keys(state.allTags)) { if (t !== 'del' && t !== FAV_TAG) state.homepageTags.add(t); }
    saveTagConfig(); renderTagBar(); renderTagManager();
}
function tagMgrHideAll() {
    // 清空主页白名单（收藏除外，收藏始终显示）
    state.homepageTags.clear();
    saveTagConfig(); renderTagBar(); renderTagManager();
}
function tagMgrInvert() {
    // 反转：主页显示的变隐藏，隐藏的变显示
    const next = new Set();
    for (const t of Object.keys(state.allTags)) {
        if (t === 'del' || t === FAV_TAG) continue;
        if (!state.homepageTags.has(t)) next.add(t);
    }
    state.homepageTags = next;
    saveTagConfig(); renderTagBar(); renderTagManager();
}

function filterByTagFromMgr(tag) {
    closeTagManager();
    state.activeTags.clear();
    state.activeTags.add(tag);
    state.activeTag = tag;
    renderHomepageTags();
    renderActiveTags();
    updateTagPickerBtn();
    applyFilters();
}

function moveTagToCat(tag, cat) {
    if (cat === '__new__') {
        const name = prompt('输入新分类名称：');
        if (!name || !name.trim()) { renderTagManager(); return; }
        cat = name.trim();
        if (!state.tagCategories[cat]) state.tagCategories[cat] = [];
    }
    if (!cat) return;
    for (const [c, tags] of Object.entries(state.tagCategories)) {
        state.tagCategories[c] = tags.filter(t => t !== tag);
        if (!state.tagCategories[c].length) delete state.tagCategories[c];
    }
    if (!state.tagCategories[cat]) state.tagCategories[cat] = [];
    if (!state.tagCategories[cat].includes(tag)) state.tagCategories[cat].push(tag);
    saveTagConfig();
    renderTagManager();
}

function createTagCategory() {
    const input = $('tagMgrNewCat');
    const name = (input.value || '').trim();
    if (!name) return;
    if (!state.tagCategories[name]) state.tagCategories[name] = [];
    saveTagConfig();
    input.value = '';
    renderTagManager();
}

function deleteTagCategory(cat) {
    showConfirm(icon('trash'), `删除分类「${cat}」？`, '标签不会被删除。', '确认删除', () => {
        delete state.tagCategories[cat];
        saveTagConfig();
        renderTagManager();
    });
}

function renameTag(oldTag) {
    const newName = prompt(`重命名标签「${oldTag}」为：`, oldTag);
    if (!newName || !newName.trim() || newName.trim() === oldTag) return;
    const trimmed = newName.trim();
    showConfirm(icon('edit'), `重命名「${oldTag}」→「${trimmed}」？`, `将更新所有漫画上的该标签。`, '确认重命名', async () => {
        try {
            const res = await apiPost('rename-tag', { oldName: oldTag, newName: trimmed });
            if (res.ok) {
                // 更新本地状态
                if (state.allTags[oldTag] !== undefined) {
                    state.allTags[trimmed] = state.allTags[oldTag];
                    delete state.allTags[oldTag];
                }
                if (state.homepageTags.has(oldTag)) {
                    state.homepageTags.delete(oldTag);
                    state.homepageTags.add(trimmed);
                }
                for (const [cat, tags] of Object.entries(state.tagCategories)) {
                    const idx = tags.indexOf(oldTag);
                    if (idx >= 0) tags[idx] = trimmed;
                }
                // 更新漫画数据中的 tags
                for (const list of state.comics) {
                    if (!list) continue;
                    for (const c of list) {
                        const idx = (c.tags || []).indexOf(oldTag);
                        if (idx >= 0) c.tags[idx] = trimmed;
                    }
                }
                if (state.activeTags.has(oldTag)) {
                    state.activeTags.delete(oldTag);
                    state.activeTags.add(trimmed);
                }
                saveTagConfig();
                renderTagBar();
                renderTagManager();
                applyFilters();
                showToast(`已重命名 ${res.renamed} 处`, 'success');
            } else {
                showToast(res.error || '重命名失败', 'error');
            }
        } catch (e) { showToast('网络错误', 'error'); }
    });
}

function deleteTag(tagName) {
    showConfirm(icon('trash'), `全局删除标签「${tagName}」？`, `将从所有漫画上移除此标签，无法恢复。`, '确认删除', async () => {
        try {
            const res = await apiPost('delete-tag', { name: tagName });
            if (res.ok) {
                // 更新本地状态
                delete state.allTags[tagName];
                state.homepageTags.delete(tagName);
                for (const [cat, tags] of Object.entries(state.tagCategories)) {
                    state.tagCategories[cat] = tags.filter(t => t !== tagName);
                    if (!state.tagCategories[cat].length) delete state.tagCategories[cat];
                }
                for (const list of state.comics) {
                    if (!list) continue;
                    for (const c of list) {
                        if (c.tags) c.tags = c.tags.filter(t => t !== tagName);
                    }
                }
                state.activeTags.delete(tagName);
                saveTagConfig();
                renderTagBar();
                renderTagManager();
                applyFilters();
                showToast(`已从 ${res.removed} 本漫画移除`, 'success');
            } else {
                showToast(res.error || '删除失败', 'error');
            }
        } catch (e) { showToast('网络错误', 'error'); }
    });
}

// ==================== 阅读器 ====================

async function openComic(pIdx, cIdx) {
    const comic = (state.comics[pIdx] || [])[cIdx];
    if (!comic) return;
    hidePreview();
    const path = state.paths[pIdx], name = comic.name;
    try {
        const data = await api('pages', { path, comic: name });
        state.cur = { p: path, n: name, pgs: data.pages || [] };
        // 恢复阅读进度
        const saved = comic.readProgress || 0;
        state.idx = (saved > 0 && saved < (data.pages || []).length) ? saved : 0;
        state.dbl = window.innerWidth > window.innerHeight;
        $('modeBtn').innerText = state.dbl ? '双' : '单';
        
        $('reader').classList.remove('hidden');
        document.body.style.overflow = 'hidden';
        state.readerOpen = true;
        history.pushState({ reader: true }, '', '#reader');
        comic.lastRead = Date.now();
        apiPost('meta', { path, name, lastRead: comic.lastRead });
        renderPages();
        showReaderUI();
    } catch (e) {
        console.error('打开漫画失败:', e);
    }
}

async function renderPages() {
    const t = ++state.token, { p, n, pgs } = state.cur, i = state.idx, total = pgs.length;
    const step = state.dbl ? 2 : 1;
    $('pageProgress').innerText = `${i + 1}${state.dbl && i + 1 < total ? '-' + Math.min(i + 2, total) : ''} / ${total}`;
    updatePageSlider();
    const getUrl = idx => idx >= 0 && idx < total ? `/api/image?${new URLSearchParams({ path: p, comic: n, file: pgs[idx] }).toString()}` : null;
    const loadImg = url => url ? new Promise(res => { const img = new Image(); img.className = 'comic-img'; img.onload = () => res(img); img.onerror = () => res(null); img.src = url; }) : Promise.resolve(null);
    const imgs = await Promise.all([loadImg(getUrl(i)), state.dbl ? loadImg(getUrl(i + 1)) : null]);
    if (t !== state.token) return;
    const b0 = $('box-0'), b1 = $('box-1');
    b0.innerHTML = ''; b1.innerHTML = '';
    $('viewContainer').className = `view-container ${state.dbl ? 'is-double' : 'is-single'}`;
    b1.style.display = state.dbl ? "flex" : "none";
    if (state.dbl) {
        const [l, r] = state.rtl ? [b1, b0] : [b0, b1];
        if (imgs[0]) l.appendChild(imgs[0]);
        if (imgs[1]) r.appendChild(imgs[1]);
    } else if (imgs[0]) b0.appendChild(imgs[0]);
    // prefetch 下几页
    for (let x = i + step; x < i + step + 4 && x < total; x++) {
        const u = getUrl(x);
        if (u) { const pf = new Image(); pf.src = u; }
    }
}

function initPageSlider() {
    const slider = $('pageSlider');
    slider.addEventListener('input', () => {
        showReaderUI(); // 拖动时重置隐藏计时
        const val = parseInt(slider.value);
        if (!val || val < 1 || val > state.cur.pgs.length) return;
        state.idx = val - 1;
        renderPages();
        saveReadProgress();
    });
    // 拖动期间不自动消失
    slider.addEventListener('mousedown', () => clearTimeout(state.uiTimer));
    slider.addEventListener('touchstart', () => clearTimeout(state.uiTimer), { passive: true });
    slider.addEventListener('mouseup', () => showReaderUI());
    slider.addEventListener('touchend', () => showReaderUI());
}
function updatePageSlider() {
    const slider = $('pageSlider');
    const total = state.cur.pgs.length;
    slider.max = total;
    slider.value = state.idx + 1;
    slider.parentElement.classList.toggle('rtl', state.rtl);
}

let _saveProgressTimer = null;
function saveReadProgress() {
    clearTimeout(_saveProgressTimer);
    _saveProgressTimer = setTimeout(() => {
        if (!state.readerOpen) return;
        // 用路径+名字定位漫画
        const pIdx = state.paths.indexOf(state.cur.p);
        const comic = pIdx >= 0 ? (state.comics[pIdx] || []).find(c => c.name === state.cur.n) : null;
        if (comic) comic.readProgress = state.idx;
        apiPost('meta', { path: state.cur.p, name: state.cur.n, readProgress: state.idx });
        updateProgressButton();
    }, 500);
}

function turnPage(dir) {
    const step = state.dbl ? 2 : 1;
    const next = state.idx + (dir * step);
    if (next >= 0 && next < state.cur.pgs.length) {
        state.idx = next;
        saveReadProgress();
        renderPages();
    }
    else if (next < 0) state.idx = 0;
}

function handleAction(clientX) {
    const now = Date.now();
    if (!state.isMobile && now - state.lastTap < 300) { toggleFS(); state.lastTap = 0; return; }
    state.lastTap = now;
    setTimeout(() => {
        if (state.lastTap === 0) return;
        const w = window.innerWidth;
        if (clientX > w * 0.7) turnPage(state.rtl ? -1 : 1);
        else if (clientX < w * 0.3) turnPage(state.rtl ? 1 : -1);
        else { toggleReaderUI(); }
    }, 300);
}

// reader 事件绑定移到 initApp() 中，确保 DOM 已就绪

function showReaderUI() {
    $('reader').classList.add('show-ui');
    clearTimeout(state.uiTimer);
    state.uiTimer = setTimeout(() => $('reader').classList.remove('show-ui'), 3500);
}
function toggleReaderUI() {
    $('reader').classList.contains('show-ui') ? $('reader').classList.remove('show-ui') : showReaderUI();
}

function closeReader(fromPopstate) {
    // 取消待保存的 debounce，避免用旧进度覆盖
    clearTimeout(_saveProgressTimer);
    // 关闭前保存进度
    if (state.readerOpen && state.cur.p && state.cur.n) {
        const pIdx = state.paths.indexOf(state.cur.p);
        const comic = pIdx >= 0 ? (state.comics[pIdx] || []).find(c => c.name === state.cur.n) : null;
        if (comic) comic.readProgress = state.idx;
        apiPost('meta', { path: state.cur.p, name: state.cur.n, readProgress: state.idx });
    }
    clearTimeout(state.uiTimer);
    $('reader').classList.remove('show-ui');
    $('reader').classList.add('hidden');
    document.body.style.overflow = '';
    state.readerOpen = false;
    if (!fromPopstate && history.state && history.state.reader) history.back();
}
function toggleMode() { state.dbl = !state.dbl; $('modeBtn').innerText = state.dbl ? '双' : '单'; renderPages(); }
function toggleDir() { state.rtl = !state.rtl; $('dirBtn').innerText = state.rtl ? 'RTL' : 'LTR'; renderPages(); }
function toggleSidebar() {
    const sidebar = $('sidebar');
    const backdrop = $('sidebarBackdrop');
    sidebar.classList.toggle('open');
    if (backdrop) backdrop.classList.toggle('show', sidebar.classList.contains('open'));
}
function closeSidebar() {
    const sidebar = $('sidebar');
    const backdrop = $('sidebarBackdrop');
    sidebar.classList.remove('open');
    if (backdrop) backdrop.classList.remove('show');
}
function toggleSidebarCollapse() {
    const app = document.querySelector('.app');
    app.classList.toggle('sidebar-collapsed');
    updateCollapseBtn();
}
function updateCollapseBtn() {
    const btn = $('collapseBtn');
    if (!btn) return;
    const collapsed = document.querySelector('.app').classList.contains('sidebar-collapsed');
    btn.innerHTML = collapsed ? icon('chevronRight') : icon('chevronLeft');
    btn.title = collapsed ? '展开侧栏' : '收缩侧栏';
}
function initSidebarCollapse() {
    // 每次加载页面默认收起
    document.querySelector('.app').classList.add('sidebar-collapsed');
    updateCollapseBtn();
}
function scrollToTop() {
    $('bookshelf').scrollTo({ top: 0, behavior: 'smooth' });
    if (state.isMobile) closeSidebar();
}
function toggleFS() { toggleAppFullscreen(); }

// ==================== 应用全屏 ====================

function enterAppFullscreen() {
    state.appFullscreen = true;
    document.documentElement.requestFullscreen().catch(() => { state.appFullscreen = false; });
    updateFullscreenBtns();
}
function exitAppFullscreen() {
    state.appFullscreen = false;
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    updateFullscreenBtns();
}
function toggleAppFullscreen() {
    if (state.appFullscreen) exitAppFullscreen();
    else enterAppFullscreen();
}
function updateFullscreenBtns() {
    document.querySelectorAll('[data-action="toggleFullscreen"]').forEach(el => {
        el.classList.toggle('active', state.appFullscreen);
    });
}
function handleEscAction() {
    if (!$('reader').classList.contains('hidden')) { closeReader(); return; }
    if (!$('contextMenu').classList.contains('hidden')) { $('contextMenu').classList.add('hidden'); return; }
    if (!$('recycleMenu').classList.contains('hidden')) { $('recycleMenu').classList.add('hidden'); return; }
    if (!$('tagOverlay').classList.contains('hidden')) { closeTagModal(); return; }
    if (!$('tagMgrOverlay').classList.contains('hidden')) { closeTagManager(); return; }
    if (!$('tagPickerOverlay').classList.contains('hidden')) { closeTagPicker(); return; }
    if (!$('jmOverlay').classList.contains('hidden')) { closeJmPanel(); return; }
    if (!$('randomOverlay').classList.contains('hidden')) { closeRandom(); return; }
    if (!$('recycleOverlay').classList.contains('hidden')) { closeRecycleBin(); return; }
}
// 浏览器退出全屏时（ESC 触发），若应用全屏仍开启则重新进入并执行 ESC 动作
document.addEventListener('fullscreenchange', () => {
    if (!document.fullscreenElement && state.appFullscreen) {
        document.documentElement.requestFullscreen().catch(() => { state.appFullscreen = false; updateFullscreenBtns(); });
        handleEscAction();
    }
});

// ==================== 随机漫画 ====================

function buildAllComics() {
    state.random.allComics = [];
    for (let i = 0; i < state.paths.length; i++) (state.comics[i] || []).forEach(c => {
        if (!(c.tags || []).includes('del')) state.random.allComics.push({ pIdx: i, name: c.name, cover: c.cover });
    });
}

async function randomComic() {
    hidePreview();
    if (!state.random.allComics.length) buildAllComics();
    if (!state.random.allComics.length) return;
    await showRandomCard();
}
async function rerollRandom() { await showRandomCard(); }

async function showRandomCard() {
    const pool = state.random.allComics;
    if (!pool.length) return;
    let pick; do { pick = Math.floor(Math.random() * pool.length); } while (pool.length > 1 && pick === state.random.cIdx);
    const item = pool[pick]; state.random.pIdx = item.pIdx; state.random.cIdx = pick;
    const path = state.paths[item.pIdx], name = item.name;
    $('randomCover').src = ''; $('randomTitle').innerText = name; $('randomMeta').innerText = path;
    $('randomOverlay').classList.remove('hidden');
    requestAnimationFrame(() => { $('randomOverlay').classList.add('visible'); $('randomCard').classList.add('animate-in'); });
    // 优先用本地 comics 数据中的 cover，避免额外 pages 请求
    const comic = (state.comics[item.pIdx] || []).find(c => c.name === name);
    if (comic?.cover) {
        $('randomCover').src = `/api/image?${new URLSearchParams({ path, comic: name, file: comic.cover }).toString()}`;
    } else {
        try { const data = await api('pages', { path, comic: name }); if (data.pages?.length) $('randomCover').src = `/api/image?${new URLSearchParams({ path, comic: name, file: data.pages[0] }).toString()}`; } catch (e) { }
    }
}

function closeRandom() {
    $('randomOverlay').classList.remove('visible');
    setTimeout(() => { $('randomOverlay').classList.add('hidden'); $('randomCard').classList.remove('animate-in'); }, 300);
}

async function openRandomComic() {
    const pIdx = state.random.pIdx, name = state.random.allComics[state.random.cIdx]?.name;
    if (pIdx < 0 || !name) return;
    const cIdx = (state.comics[pIdx] || []).findIndex(c => c.name === name);
    if (cIdx < 0) return;
    closeRandom(); setTimeout(() => openComic(pIdx, cIdx), 350);
}

// ==================== 下载状态面板 ====================

function openDlStatus() {
    const overlay = $('dlOverlay');
    overlay.classList.remove('hidden');
    requestAnimationFrame(() => { overlay.classList.add('visible'); $('dlPanel').classList.add('animate-in'); });
    renderDlStatus();
}

function closeDlStatus() {
    const overlay = $('dlOverlay');
    overlay.classList.remove('visible');
    setTimeout(() => { overlay.classList.add('hidden'); $('dlPanel').classList.remove('animate-in'); }, 300);
}

async function renderDlStatus(cachedData) {
    const body = $('dlPanelBody');
    if (!body) return;
    try {
        const data = cachedData || await api('download/history');
        const { active = [], completed = [], errors = [] } = data;
        if (!active.length && !completed.length && !errors.length) {
            body.innerHTML = '<div class="dl-panel-empty">暂无下载记录</div>';
            return;
        }
        let html = '';
        if (active.length) {
            html += `<div class="dl-panel-sep">正在下载 (${active.length})</div>`;
            active.forEach(t => {
                html += `<div class="dl-panel-item is-active">
                    <span class="p-id">JM${esc(t.id)}</span>
                    <span class="p-name">${esc(t.name || '...')}</span>
                    <span class="p-status"><span class="dl-spinner" style="display:inline-block;width:12px;height:12px;border-width:2px;vertical-align:middle;margin-right:4px"></span>${esc(t.progress || '下载中')}</span>
                </div>`;
            });
        }
        const history = [...completed, ...errors].sort((a, b) => (b.download_time || 0) - (a.download_time || 0));
        if (history.length) {
            html += `<div class="dl-panel-sep">下载记录 (${history.length})</div>`;
            history.forEach(t => {
                const isDone = !t.error;
                const rel = relTime(t.download_time);
                html += `<div class="dl-panel-item ${isDone ? 'is-done' : 'is-error'}">
                    <span class="p-id">JM${esc(t.id)}</span>
                    <span class="p-name">${esc(t.name || '')}</span>
                    <span class="p-status">${rel ? esc(rel) + ' ' : ''}${isDone ? icon('checkCircle') : icon('xCircle')}</span>
                </div>`;
            });
        }
        body.innerHTML = html;
    } catch (e) {
        body.innerHTML = '<div class="dl-panel-empty">加载失败</div>';
    }
}

// ==================== JM 下载器 ====================

function openJmPanel() {
    hidePreview();
    $('jmOverlay').classList.remove('hidden');
    $('jmPanel').classList.remove('minimized');
    $('jmBody').classList.remove('collapsed');
    $('jmMinBtn').innerHTML = icon('chevronDown');
    requestAnimationFrame(() => { $('jmOverlay').classList.add('visible'); $('jmPanel').classList.add('animate-in'); });
    renderJmHistory();
    renderDownloadHistory();
    $('jmInput').focus();
}

function closeJmPanel() {
    // 不再清除下载定时器，保持后台轮询
    $('jmOverlay').classList.remove('visible');
    setTimeout(() => { $('jmOverlay').classList.add('hidden'); $('jmPanel').classList.remove('animate-in'); }, 300);
}

function toggleMinimizeJm() {
    const panel = $('jmPanel');
    const body = $('jmBody');
    const btn = $('jmMinBtn');
    if (panel.classList.contains('minimized')) {
        panel.classList.remove('minimized');
        body.classList.remove('collapsed');
        btn.innerHTML = icon('chevronDown');
    } else {
        panel.classList.add('minimized');
        body.classList.add('collapsed');
        btn.innerHTML = icon('chevronUp');
    }
}

function onJmInput() {
    const el = $('jmInput');
    const v = el.value;
    const cleaned = v.replace(/jm(\d+)/gi, '$1');
    if (cleaned !== v) el.value = cleaned;
}

async function doJmSearch() {
    const raw = $('jmInput').value.trim();
    if (!raw) return;
    const tokens = raw.split(/[,，\s\n]+/).filter(Boolean);
    const ids = tokens.map(t => t.replace(/^(jm)/i, '').trim()).filter(t => /^\d+$/.test(t));
    if (!ids.length) {
    $('jmResult').innerHTML = `<div class="jm-error">请输入有效的 JM 号</div>`; return; }

    if (ids.length > 1) {
        // 多个 ID 时直接批量下载
    $('jmResult').innerHTML = `<div class="jm-loading"><div class="search-spinner"></div><p>正在启动 ${ids.length} 个下载任务...</p><p style="font-size:12px;color:var(--text-3);margin-top:4px">将并行下载，关闭页面不影响进度</p></div>`;
        try {
            const data = await apiPost('download', { ids });
            if (data.error) {
    $('jmResult').innerHTML = `<div class="jm-error">${esc(data.error)}</div>`; return; }
            const startedIds = data.ids || ids;
            // 渲染批量下载进度面板
            let batchHtml = `<div class="jm-batch-panel">
                <div class="jm-batch-title">${icon('download')} 正在下载 ${startedIds.length} 本漫画</div>
                <div class="jm-batch-list" id="jmBatchList">`;
            startedIds.forEach(id => {
                batchHtml += `<div class="jm-batch-item" id="batch-${id}">
                    <span class="jm-batch-id">JM${esc(id)}</span>
                    <span class="jm-batch-status"><div class="dl-spinner" style="display:inline-block;width:14px;height:14px"></div> 等待中</span>
                </div>`;
            });
            batchHtml += `</div><div class="jm-batch-summary" id="jmBatchSummary">完成 0 / ${startedIds.length}</div></div>`;
    $('jmResult').innerHTML = batchHtml;
            startedIds.forEach(id => { addToHistory(id, ''); pollBatchStatus(id, startedIds.length); });
        } catch (e) {
    $('jmResult').innerHTML = `<div class="jm-error">网络错误: ${esc(e.message)}</div>`; }
        return;
    }

    const id = ids[0];
    $('jmResult').innerHTML = `<div class="jm-loading"><div class="search-spinner"></div><p>正在搜索 JM${esc(id)}...</p></div>`;
    try {
        const data = await api('search', { q: id });
        if (data.error) {
    $('jmResult').innerHTML = `<div class="jm-error">${esc(data.error)}</div>`; return; }
        const tags = (data.tags || []).slice(0, 5).map(t => `<span class="search-tag">${esc(t)}</span>`).join('');
    $('jmResult').innerHTML = `
            <div class="jm-result-card">
                ${data.cover ? `<div class="jm-result-cover-wrap"><img class="jm-result-cover" src="${esc(data.cover)}" alt="封面" loading="lazy"></div>` : ''}
                <div class="jm-result-name">${esc(data.name)}</div>
                <div class="jm-result-id">JM${esc(String(data.id))}${data.author ? ' · ' + esc(data.author) : ''}${data.episode_count ? ' · ' + data.episode_count + ' 章' : ''}</div>
                ${tags ? `<div class="jm-result-tags">${tags}</div>` : ''}
                ${data.downloaded
                    ? `<div class="dl-done" style="margin-top:12px">${icon('checkCircle')} 已下载到本地</div>`
                    : `<button class="jm-download-btn" data-action="startJmDownload" data-id="${esc(String(data.id))}" data-name="${esc(data.name)}">${icon('download')} 下载到本地</button>`
                }
                <div class="search-dl-status" id="dl-status-${esc(String(data.id))}"></div>
            </div>
        `;
        pollDownloadStatus(data.id);
        addToHistory(data.id, data.name);
    } catch (e) {
    $('jmResult').innerHTML = `<div class="jm-error">网络错误: ${esc(e.message)}</div>`; }
}

function addToHistory(id, name) {
    state.jmHistory = state.jmHistory.filter(h => h.id !== id);
    state.jmHistory.unshift({ id, name, time: Date.now() });
    if (state.jmHistory.length > 20) state.jmHistory.length = 20;
    renderJmHistory();
}

function renderJmHistory() {
    if (!state.jmHistory.length) { $('jmHistory').innerHTML = ''; return; }
    $('jmHistory').innerHTML = `<div class="jm-hist-label">最近搜索：</div>` +
        state.jmHistory.map(h => `<button class="jm-hist-pill" data-action="jmHistSearch" data-arg1="${esc(h.id)}">JM${esc(h.id)} ${esc(h.name || '')}</button>`).join('');
}

async function renderDownloadHistory() {
    try {
        const data = await api('download/history');
        const { active = [], completed = [], errors = [], total = 0 } = data;
        if (!total) {
            $('jmQueue').innerHTML = '';
            return;
        }
        let html = '';
        if (active.length) {
            html += `<div class="jm-hist-label">${icon('download')} 正在下载 (${active.length})</div>`;
            html += active.map(t => `<div class="jm-queue-item is-downloading">
                <span class="queue-id">JM${esc(t.id)}</span>
                <span class="queue-name">${esc(t.name || '...')}</span>
                <span class="queue-status"><div class="dl-spinner" style="display:inline-block;width:12px;height:12px;vertical-align:middle;margin-right:4px"></div>${esc(t.progress || '下载中')}</span>
            </div>`).join('');
        }
        if (completed.length) {
            html += `<div class="jm-hist-label">${icon('checkCircle')} 下载完成 (${completed.length})</div>`;
            html += completed.map(t => {
                const rel = relTime(t.download_time);
                return `<div class="jm-queue-item is-done">
                    <span class="queue-id">JM${esc(t.id)}</span>
                    <span class="queue-name">${esc(t.name || '')}</span>
                    <span class="queue-status">${rel ? `<span style="opacity:.5;font-size:11px;margin-right:6px">${esc(rel)}</span>` : ''}${icon('checkCircle')} 完成</span>
                </div>`;
            }).join('');
        }
        if (errors.length) {
            html += `<div class="jm-hist-label">${icon('xCircle')} 下载失败 (${errors.length})</div>`;
            html += errors.map(t => `<div class="jm-queue-item is-error">
                <span class="queue-id">JM${esc(t.id)}</span>
                <span class="queue-name">${esc(t.name || '')}</span>
                <span class="queue-status">${icon('xCircle')} ${esc((t.error || t.progress || '失败').slice(0, 60))}</span>
            </div>`).join('');
        }
        $('jmQueue').innerHTML = html;

        // 如果有活跃任务，恢复结果区的轮询 UI
        if (active.length > 0) {
            const resultEl = $('jmResult');
            if (!resultEl) return;
            const hasStatus = resultEl.querySelector('.dl-progress, .dl-done, .dl-error, .jm-batch-panel');
            if (!hasStatus) {
                const ids = active.map(t => t.id);
                if (ids.length === 1) {
                    const id = ids[0];
                    resultEl.innerHTML = `<div class="jm-result-card">
                        <div class="jm-result-id">JM${esc(id)}</div>
                        <div class="dl-progress"><div class="dl-spinner"></div> 正在下载...</div>
                        <div class="search-dl-status" id="dl-status-${esc(id)}"></div>
                    </div>`;
                    if (!state.dlTimers[id]) pollDownloadStatus(id);
                } else {
                    let batchHtml = `<div class="jm-batch-panel">
                        <div class="jm-batch-title">${icon('download')} 下载任务进行中</div>
                        <div class="jm-batch-list" id="jmBatchList">`;
                    ids.forEach(id => {
                        batchHtml += `<div class="jm-batch-item" id="batch-${id}">
                            <span class="jm-batch-id">JM${esc(id)}</span>
                            <span class="jm-batch-status"><div class="dl-spinner" style="display:inline-block;width:14px;height:14px"></div> 查询中...</span>
                        </div>`;
                    });
                    batchHtml += `</div><div class="jm-batch-summary" id="jmBatchSummary">正在恢复状态...</div></div>`;
                    resultEl.innerHTML = batchHtml;
                    ids.forEach(id => { if (!state.dlTimers[id]) pollBatchStatus(id, ids.length); });
                }
            }
        }
    } catch (e) { /* ignore */ }
    if ($('dlOverlay') && $('dlOverlay').classList.contains('visible')) renderDlStatus(data);
}

async function startJmDownload(jmId, name) {
    state.activeJmDownloads.add(jmId);
    updateDownloadBadge(state.activeJmDownloads.size);
    const statusEl = $(`dl-status-${jmId}`);
    if (statusEl) statusEl.innerHTML = `<div class="dl-progress"><div class="dl-spinner"></div> 正在启动...</div>`;
    try {
        const data = await apiPost('download', { ids: [jmId] });
        if (data.error) { if (statusEl) statusEl.innerHTML = `<div class="dl-error">${icon('xCircle')} ${esc(data.error)}</div>`; state.activeJmDownloads.delete(jmId); updateDownloadBadge(state.activeJmDownloads.size); return; }
        pollDownloadStatus(jmId);
    } catch (e) { if (statusEl) statusEl.innerHTML = `<div class="dl-error">${icon('xCircle')} ${esc(e.message)}</div>`; state.activeJmDownloads.delete(jmId); updateDownloadBadge(state.activeJmDownloads.size); }
}

function pollDownloadStatus(jmId) {
    state.activeJmDownloads.add(jmId);
    if (state.dlTimers[jmId]) clearInterval(state.dlTimers[jmId]);
    state.dlTimers[jmId] = setInterval(async () => {
        try {
            const data = await api('download/status', { id: jmId });
            const statusEl = $(`dl-status-${jmId}`);
            // 如果面板关闭且 DOM 元素不存在，停止轮询（交给全局轮询）
            if (!statusEl && !$('jmOverlay').classList.contains('visible') && !state.activeJmDownloads.has(jmId)) { clearInterval(state.dlTimers[jmId]); delete state.dlTimers[jmId]; return; }
            if (data.status === 'none') { clearInterval(state.dlTimers[jmId]); delete state.dlTimers[jmId]; state.activeJmDownloads.delete(jmId); updateDownloadBadge(state.activeJmDownloads.size); return; }
            if (data.status === 'pending' || data.status === 'downloading') {
                if (statusEl) statusEl.innerHTML = `<div class="dl-progress"><div class="dl-spinner"></div>${data.name ? `<span class="dl-name">${esc(data.name)}</span>` : ''}<span>${esc(data.progress)}</span></div>`;
            } else if (data.status === 'done') {
                clearInterval(state.dlTimers[jmId]); delete state.dlTimers[jmId]; state.activeJmDownloads.delete(jmId);
                updateDownloadBadge(state.activeJmDownloads.size);
                if (statusEl) statusEl.innerHTML = `<div class="dl-done">${icon('checkCircle')} ${data.name ? esc(data.name) : '下载完成'}</div>`;
                refreshBookshelfIncremental();
            } else if (data.status === 'error') {
                clearInterval(state.dlTimers[jmId]); delete state.dlTimers[jmId]; state.activeJmDownloads.delete(jmId);
                updateDownloadBadge(state.activeJmDownloads.size);
                if (statusEl) statusEl.innerHTML = `<div class="dl-error">${icon('xCircle')} ${esc(data.progress)}</div>`;
            }
        } catch (e) { }
    }, 1500);
}

function pollBatchStatus(jmId, totalCount) {
    state.activeJmDownloads.add(jmId);
    if (state.dlTimers[jmId]) clearInterval(state.dlTimers[jmId]);
    state.dlTimers[jmId] = setInterval(async () => {
        try {
            const data = await api('download/status', { id: jmId });
            const itemEl = $(`batch-${jmId}`);
            if (!itemEl) { clearInterval(state.dlTimers[jmId]); delete state.dlTimers[jmId]; state.activeJmDownloads.delete(jmId); updateDownloadBadge(state.activeJmDownloads.size); return; }
            if (data.status === 'none') { clearInterval(state.dlTimers[jmId]); delete state.dlTimers[jmId]; state.activeJmDownloads.delete(jmId); updateDownloadBadge(state.activeJmDownloads.size); return; }
            if (data.status === 'pending' || data.status === 'downloading') {
                const name = data.name ? esc(data.name) : `JM${jmId}`;
                itemEl.querySelector('.jm-batch-status').innerHTML = `<div class="dl-spinner" style="display:inline-block;width:14px;height:14px"></div> ${name} ${esc(data.progress)}`;
            } else if (data.status === 'done') {
                clearInterval(state.dlTimers[jmId]); delete state.dlTimers[jmId]; state.activeJmDownloads.delete(jmId);
                updateDownloadBadge(state.activeJmDownloads.size);
                const name = data.name ? esc(data.name) : `JM${jmId}`;
                itemEl.querySelector('.jm-batch-status').innerHTML = `${icon('checkCircle')} ${name} 完成`;
                itemEl.classList.add('done');
                checkBatchComplete(totalCount);
                refreshBookshelfIncremental();
            } else if (data.status === 'error') {
                clearInterval(state.dlTimers[jmId]); delete state.dlTimers[jmId]; state.activeJmDownloads.delete(jmId);
                updateDownloadBadge(state.activeJmDownloads.size);
                itemEl.querySelector('.jm-batch-status').innerHTML = `${icon('xCircle')} ${esc(data.progress)}`;
                itemEl.classList.add('error');
                checkBatchComplete(totalCount);
            }
        } catch (e) { }
    }, 1500);
}

function checkBatchComplete(totalCount) {
    const list = $('jmBatchList');
    if (!list) return;
    const doneCount = list.querySelectorAll('.done').length;
    const errorCount = list.querySelectorAll('.error').length;
    const finished = doneCount + errorCount;
    const summary = $('jmBatchSummary');
    if (summary) summary.textContent = `完成 ${doneCount} / ${totalCount}${errorCount ? `（${errorCount} 个失败）` : ''}`;
    if (finished >= totalCount) {
        // 全部完成，显示汇总
        const panel = document.querySelector('.jm-batch-panel');
        if (panel) {
            const title = panel.querySelector('.jm-batch-title');
            if (title) title.innerHTML = doneCount === totalCount
                ? `全部 ${totalCount} 本下载完成！`
                : `${icon('download')} 下载完成：${doneCount} 成功，${errorCount} 失败`;
            // 在底部加刷新按钮
            if (summary) summary.innerHTML += ` <button class="dl-refresh-btn" onclick="location.reload()" style="margin-left:8px">${icon('refresh')} 刷新书架</button>`;
        }
    }
}

// ==================== 主题切换（已迁移到 app.js 的深浅双主题） ====================

window.onload = init;
