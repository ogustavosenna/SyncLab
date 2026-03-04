/**
 * SyncLab — Frontend Application
 * Dual-mode: PyWebView (desktop) with native drag-and-drop, or browser fallback.
 * Socket.IO real-time progress, folder path input.
 *
 * PROGRESS DESIGN:
 *   OVERALL = steady 0-100% across all phases (total job progress)
 *   STEP    = per-video cycling (resets for each new video, shows what's being analyzed NOW)
 */

/* ============================================================
   Prevent browser from navigating on any drag-and-drop
   ============================================================ */
// Prevent browser from opening dropped files — only preventDefault, NOT stopPropagation
// (stopPropagation would block card-level drop handlers from firing)
document.addEventListener('dragover', (e) => { e.preventDefault(); });
document.addEventListener('drop',     (e) => { e.preventDefault(); });

const _splashStart = Date.now();


/* ============================================================
   PyWebView Detection
   ============================================================ */
function isPyWebView() {
    return !!(window.pywebview && window.pywebview.api);
}

function waitForPyWebView(timeout = 3000) {
    return new Promise((resolve) => {
        if (isPyWebView()) { resolve(true); return; }
        const start = Date.now();
        const check = setInterval(() => {
            if (isPyWebView()) { clearInterval(check); resolve(true); }
            else if (Date.now() - start > timeout) { clearInterval(check); resolve(false); }
        }, 100);
    });
}


/* ============================================================
   State
   ============================================================ */
const state = {
    videoFolders: [],
    audioFolders: [],
    syncing: false,
    results: null,
    config: {},
};

// Per-video step progress tracking
let _lastVi = -1;
let _stepFillTimer = null;
let _stepPct = 0;


/* ============================================================
   DOM
   ============================================================ */
const $ = (id) => document.getElementById(id);

const dom = {
    videoPathInput:  $('videoPathInput'),
    audioPathInput:  $('audioPathInput'),
    videoCard:       $('videoCard'),
    audioCard:       $('audioCard'),
    videoCount:      $('videoCount'),
    audioCount:      $('audioCount'),
    inputSection:    $('inputSection'),
    syncControls:    $('syncControls'),
    syncBtn:         $('syncBtn'),
    progressSection: $('progressSection'),
    overallPercent:  $('overallPercent'),
    overallBar:      $('overallBar'),
    overallDetail:   $('overallDetail'),
    stepPercent:     $('stepPercent'),
    stepBar:         $('stepBar'),
    stepDetail:      $('stepDetail'),
    cancelBtn:       $('cancelBtn'),
    resultsSection:  $('resultsSection'),
    resultsSummary:  $('resultsSummary'),
    resultsList:     $('resultsList'),
    exportBtn:       $('exportBtn'),
    newSyncBtn:      $('newSyncBtn'),
    settingsBtn:     $('settingsBtn'),
    settingsModal:   $('settingsModal'),
    closeSettings:   $('closeSettings'),
    saveSettings:    $('saveSettings'),
    resetSettings:   $('resetSettings'),
    settingFps:      $('settingFps'),
    settingWidth:    $('settingWidth'),
    settingHeight:   $('settingHeight'),
    settingThreshold:$('settingThreshold'),
    settingVideoExt: $('settingVideoExt'),
    settingAudioExt: $('settingAudioExt'),
    supportExportBtn:$('supportExportBtn'),
    toastContainer:  $('toastContainer'),
};


/* ============================================================
   Socket.IO
   ============================================================ */
const socket = io();

socket.on('connect',    () => console.log('[SyncLab] Connected'));
socket.on('disconnect', () => console.log('[SyncLab] Disconnected'));
socket.on('connected',  (d) => console.log('[SyncLab] Ack:', d));

socket.on('sync_started', (data) => {
    state.syncing = true;
    _lastVi = -1;
    _stepPct = 0;
    showProgress();
    setOverall(0, `Starting... ${data.total_videos} videos, ${data.total_audio_groups} audio groups`);
    setStep(0, 'Initializing...');
});

socket.on('phase', (data) => {
    const names = {
        metadata:                'Phase 1 of 3 — Reading file metadata',
        timestamp_calibration:   'Phase 2 of 3 — Calibrating timestamps',
        audio_sync:              'Phase 3 of 3 — Audio synchronization',
        brute_force:             'Extra pass — Matching unassigned videos',
        done:                    '✅ Complete!',
    };
    setOverall(data.percent || 0, names[data.phase] || data.phase);

    // Reset step bar for new phase
    _lastVi = -1;
    clearInterval(_stepFillTimer);
    if (data.phase === 'metadata' || data.phase === 'timestamp_calibration') {
        // Indeterminate: show pulsing animation for fast phases
        dom.stepBar.classList.add('step-indeterminate');
        dom.stepPercent.textContent = '';
        dom.stepDetail.textContent = data.phase === 'metadata'
            ? 'Running ffprobe on each file...'
            : 'Aligning camera clock with recorder clock...';
    } else {
        dom.stepBar.classList.remove('step-indeterminate');
        setStep(0, 'Starting...');
    }
});

socket.on('progress', (data) => {
    // --- OVERALL: always update from server ---
    if (data.overall_percent !== undefined) {
        setOverall(data.overall_percent, data.overall_detail || '');
    }

    const vi = data.video_index;
    const detail = data.step_detail || '';

    if (vi !== undefined && vi >= 0) {
        // ---- Audio sync / brute force: per-video animated step ----
        dom.stepBar.classList.remove('step-indeterminate');

        if (vi !== _lastVi) {
            // New video started
            _lastVi = vi;
            clearInterval(_stepFillTimer);

            // Reset step bar and start filling animation
            _stepPct = 6;
            setStep(_stepPct, detail);

            // Gradually fill toward ~92% (completes when next video arrives)
            _stepFillTimer = setInterval(() => {
                _stepPct = Math.min(92, _stepPct + 2 + Math.random() * 5);
                dom.stepBar.style.width = `${Math.round(_stepPct)}%`;
                dom.stepPercent.textContent = `${Math.round(_stepPct)}%`;
                if (_stepPct >= 92) clearInterval(_stepFillTimer);
            }, 350);
        } else {
            // Same video, just update detail text
            if (detail) dom.stepDetail.textContent = detail;
        }
    } else if (detail) {
        // Metadata / timestamp phases: update detail text, keep indeterminate
        dom.stepDetail.textContent = detail;
    }
});

socket.on('match', (data) => addResultRow(data));

socket.on('info', (data) => console.log('[SyncLab] Info:', data.message));

socket.on('sync_complete', (summary) => {
    state.syncing = false;
    clearInterval(_stepFillTimer);
    dom.stepBar.classList.remove('step-indeterminate');
    setOverall(100, 'Synchronization complete!');
    setStep(100, 'Done');
    playCompletionChime();
    setTimeout(() => {
        showResults(summary);
        toast('success', `Done! ${summary.synced_audio || 0} audio-synced, ${summary.synced_timestamp || 0} timestamp`);
    }, 600);
});

socket.on('sync_error', (data) => {
    state.syncing = false;
    clearInterval(_stepFillTimer);
    dom.stepBar.classList.remove('step-indeterminate');
    toast('error', `Sync failed: ${data.message}`);
    hideProgress();
});

socket.on('sync_cancelled', () => {
    state.syncing = false;
    clearInterval(_stepFillTimer);
    dom.stepBar.classList.remove('step-indeterminate');
    toast('warning', 'Sync cancelled');
    hideProgress();
});


/* ============================================================
   Browse Folder — PyWebView native or Flask fallback
   ============================================================ */
async function browseFolder(targetId, btn) {
    // Always use Flask /api/browse (spawns separate tkinter process).
    // Avoids PyWebView API bridge calls that can deadlock the GUI thread.
    if (btn) { btn.disabled = true; btn.textContent = 'Opening...'; }
    try {
        const res = await fetch('/api/browse', { method: 'POST' });
        if (!res.ok) throw new Error('Browse failed');
        const data = await res.json();

        if (data.path) {
            const el = document.getElementById(targetId);
            addPath(el, data.path);
            toast('success', 'Folder added');
        }
    } catch (e) {
        toast('error', 'Could not open folder dialog');
        console.error('[SyncLab] browseFolder error:', e);
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"/></svg> Browse Folder';
        }
    }
}


/* ============================================================
   Drag-and-drop
   - PyWebView 6.x: Python handles path resolution via DOM bridge
     (pywebviewFullPath injection + evaluate_js callback)
   - Browser: JS handles path resolution + Browse Folder fallback
   ============================================================ */
function setupCardDrop(card, pathDisplay) {
    card.addEventListener('dragenter', (e) => { e.preventDefault(); card.classList.add('drag-over'); });
    card.addEventListener('dragover',  (e) => { e.preventDefault(); });
    card.addEventListener('dragleave', (e) => {
        if (!card.contains(e.relatedTarget)) card.classList.remove('drag-over');
    });

    card.addEventListener('drop', async (e) => {
        e.preventDefault();
        card.classList.remove('drag-over');

        // ---- PyWebView mode ----
        // Python handles path resolution via its DOM bridge.
        // DO NOT call stopPropagation — pywebview's bridge needs the event to propagate.
        // The Python handler will call handleNativeDrop() with the resolved path.
        if (isPyWebView()) {
            console.log('[SyncLab:Drop] PyWebView mode — Python handles path resolution');

            // Send debug info to terminal
            var dbg = { target: pathDisplay.id, files: 0, types: [] };
            if (e.dataTransfer) {
                dbg.files = e.dataTransfer.files ? e.dataTransfer.files.length : 0;
                dbg.types = Array.from(e.dataTransfer.types || []);
                if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
                    var f = e.dataTransfer.files[0];
                    dbg.fileName = f.name;
                    dbg.pywebviewFullPath = f.pywebviewFullPath || 'undefined';
                }
            }
            fetch('/api/debug_log', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ level: 'info', source: 'JS-Drop', message: JSON.stringify(dbg) }),
            }).catch(function() {});

            // Safety timeout: if Python doesn't call handleNativeDrop within 4s,
            // show a warning. The Python handler clears this via window._synclab_pending_drop = null.
            var dropTarget = (pathDisplay.id === 'audioPathInput') ? 'audio' : 'video';
            window._synclab_pending_drop = dropTarget;
            setTimeout(function() {
                if (window._synclab_pending_drop === dropTarget) {
                    window._synclab_pending_drop = null;
                    toast('warning', 'Could not read folder path. Try Browse Folder.');
                }
            }, 4000);
            return;
        }

        // ---- Browser mode ----
        e.stopPropagation();

        let resolvedPath = '';

        // Strategy 1: pywebviewFullPath on File objects
        const files = e.dataTransfer ? e.dataTransfer.files : null;
        if (files && files.length > 0) {
            const file = files[0];
            if (file.pywebviewFullPath) resolvedPath = file.pywebviewFullPath;
        }

        // Strategy 2: dataTransfer items
        if (!resolvedPath && e.dataTransfer && e.dataTransfer.items) {
            for (const item of e.dataTransfer.items) {
                if (item.kind === 'file') {
                    const f = item.getAsFile();
                    if (f && f.pywebviewFullPath) { resolvedPath = f.pywebviewFullPath; break; }
                }
            }
        }

        // Strategy 3: text data (file:// URLs)
        if (!resolvedPath && e.dataTransfer) {
            const textData = e.dataTransfer.getData('text/uri-list')
                          || e.dataTransfer.getData('text/plain')
                          || e.dataTransfer.getData('text')
                          || '';
            if (textData) {
                let candidate = textData.trim().split('\n')[0].trim();
                if (candidate.startsWith('file:///')) {
                    candidate = decodeURIComponent(candidate.replace('file:///', ''));
                }
                if (/^[A-Za-z]:[\\\/]/.test(candidate) || candidate.startsWith('/')) {
                    resolvedPath = candidate;
                }
            }
        }

        // Resolve path to folder via Flask
        if (resolvedPath) {
            try {
                const res = await fetch('/api/resolve_path', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ path: resolvedPath }),
                });
                if (res.ok) {
                    const data = await res.json();
                    if (data.folder) {
                        addPath(pathDisplay, data.folder);
                        toast('success', 'Folder added');
                        return;
                    }
                }
            } catch (_) {}
        }

        // Browser fallback: open native folder picker
        const btn = card.querySelector('.btn-browse');
        browseFolder(pathDisplay.id, btn);
    });
}

/* Global function: Python calls via evaluate_js to push resolved path */
window.handleNativeDrop = function(target, folder) {
    console.log('[SyncLab:NativeDrop] target=' + target + ' folder=' + folder);
    window._synclab_pending_drop = null;
    var el = (target === 'audio') ? dom.audioPathInput : dom.videoPathInput;
    addPath(el, folder);
    toast('success', 'Folder added');
};


/* ============================================================
   Path Display — Multi-folder path management
   ============================================================ */

function getPaths(el) {
    try { return JSON.parse(el.dataset.paths || '[]'); }
    catch (e) { return []; }
}

function addPath(el, path) {
    if (!path) return;
    const paths = getPaths(el);
    const normalized = path.replace(/\\/g, '/').toLowerCase();
    if (paths.some(p => p.replace(/\\/g, '/').toLowerCase() === normalized)) {
        toast('info', 'Folder already added');
        return;
    }
    paths.push(path);
    setPaths(el, paths);
}

function removePath(el, index) {
    const paths = getPaths(el);
    paths.splice(index, 1);
    setPaths(el, paths);
}

function setPaths(el, paths) {
    el.dataset.paths = JSON.stringify(paths);
    renderPathList(el, paths);
    persistPaths();
    onPathChange();
}

function renderPathList(el, paths) {
    if (paths.length === 0) {
        el.innerHTML = '<span class="path-placeholder">Click Browse Folder or drag here</span>';
        return;
    }
    let html = '<div class="path-list">';
    paths.forEach((p, i) => {
        html += `<div class="path-item" title="${esc(p)}">
            <span class="path-text">${esc(p)}</span>
            <button class="path-remove" data-index="${i}" title="Remove folder">&times;</button>
        </div>`;
    });
    html += '</div>';
    el.innerHTML = html;
    el.querySelectorAll('.path-remove').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            removePath(el, parseInt(btn.dataset.index));
        });
    });
}

function persistPaths() {
    const videoPaths = getPaths(dom.videoPathInput);
    const audioPaths = getPaths(dom.audioPathInput);
    fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            last_video_dirs: videoPaths,
            last_audio_dirs: audioPaths,
        }),
    }).catch(() => {});
}

/* Backward compat: setPath wraps addPath for any legacy callers */
function setPath(el, path) {
    if (path) { addPath(el, path); }
    else { setPaths(el, []); }
}

function getPath(el) {
    const paths = getPaths(el);
    return paths.length > 0 ? paths[0] : '';
}

let _scanTimeout;

function onPathChange() {
    state.videoFolders = getPaths(dom.videoPathInput);
    state.audioFolders = getPaths(dom.audioPathInput);

    const vc = state.videoFolders.length;
    const ac = state.audioFolders.length;

    // Show folder count or empty
    dom.videoCount.textContent = vc ? `${vc} folder${vc > 1 ? 's' : ''}` : '';
    dom.audioCount.textContent = ac ? `${ac} folder${ac > 1 ? 's' : ''}` : '';

    dom.syncBtn.disabled = !(vc > 0 && ac > 0);

    // Auto-scan to show file counts when both paths are filled
    clearTimeout(_scanTimeout);
    if (vc > 0 && ac > 0) {
        // Show scanning indicator immediately
        dom.videoCount.textContent = 'Scanning...';
        dom.videoCount.classList.add('scanning');
        dom.audioCount.textContent = 'Scanning...';
        dom.audioCount.classList.add('scanning');
        _scanTimeout = setTimeout(autoScan, 400);
    }
}

async function autoScan() {
    try {
        const res = await fetch('/api/scan', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ video_folders: state.videoFolders, audio_folders: state.audioFolders }),
        });
        if (!res.ok) return;
        const scan = await res.json();
        dom.videoCount.textContent = `${scan.total_videos} videos`;
        dom.videoCount.classList.remove('scanning');
        dom.audioCount.textContent = `${scan.total_audio_groups} audio groups`;
        dom.audioCount.classList.remove('scanning');
    } catch (e) {
        dom.videoCount.classList.remove('scanning');
        dom.audioCount.classList.remove('scanning');
    }
}


/* ============================================================
   Sync Flow
   ============================================================ */
async function startSync() {
    if (state.syncing) return;
    if (!state.videoFolders.length || !state.audioFolders.length) {
        toast('warning', 'Enter both video and audio folder paths.');
        return;
    }

    dom.syncBtn.disabled = true;
    dom.syncBtn.innerHTML = '<span class="btn-sync-icon">&#x23F3;</span> Scanning...';

    try {
        const scanRes = await fetch('/api/scan', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ video_folders: state.videoFolders, audio_folders: state.audioFolders }),
        });
        if (!scanRes.ok) throw new Error((await scanRes.json()).error || 'Scan failed');

        const scan = await scanRes.json();
        dom.videoCount.textContent = `${scan.total_videos} videos`;
        dom.videoCount.classList.remove('scanning');
        dom.audioCount.textContent = `${scan.total_audio_groups} audio groups`;
        dom.audioCount.classList.remove('scanning');

        if (!scan.total_videos && !scan.total_audio_groups) {
            toast('warning', 'No files found. Check paths and extensions.');
            resetSyncBtn();
            return;
        }
        if (!scan.total_videos) {
            toast('warning', 'No video files found. Check video folder paths.');
            resetSyncBtn();
            return;
        }
        if (!scan.total_audio_groups) {
            toast('warning', 'No audio groups (ZOOM folders with WAV files) found. Check audio folder path.');
            resetSyncBtn();
            return;
        }

        const syncRes = await fetch('/api/sync', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
        if (!syncRes.ok) throw new Error((await syncRes.json()).error || 'Sync start failed');

    } catch (err) {
        toast('error', err.message);
        resetSyncBtn();
    }
}

function resetSyncBtn() {
    dom.syncBtn.disabled = false;
    dom.syncBtn.innerHTML = '<svg class="btn-sync-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0114.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0020.49 15"/></svg> SYNCHRONIZE';
}


/* ============================================================
   Progress
   ============================================================ */
function showProgress() {
    dom.progressSection.style.display = 'block';
    dom.syncControls.style.display = 'none';
    dom.resultsSection.style.display = 'none';
    if (dom.inputSection) dom.inputSection.style.display = 'none';
    dom.resultsList.innerHTML = '';
}

function hideProgress() {
    dom.progressSection.style.display = 'none';
    clearInterval(_stepFillTimer);
    dom.stepBar.classList.remove('step-indeterminate');
    resetSyncBtn();
    dom.syncControls.style.display = 'block';
    if (dom.inputSection) dom.inputSection.style.display = 'block';
}

function setOverall(pct, detail) {
    const p = clamp(pct);
    dom.overallPercent.textContent = `${p}%`;
    dom.overallBar.style.width = `${p}%`;
    if (detail) dom.overallDetail.textContent = detail;
}

function setStep(pct, detail) {
    const p = clamp(pct);
    dom.stepPercent.textContent = `${p}%`;
    dom.stepBar.style.width = `${p}%`;
    if (detail) dom.stepDetail.textContent = detail;
}


/* ============================================================
   Results
   ============================================================ */
function showResults(summary) {
    dom.progressSection.style.display = 'none';
    dom.resultsSection.style.display = 'block';
    dom.syncControls.style.display = 'none';
    dom.inputSection.style.display = 'none';

    let html = '';
    if (summary.synced_audio > 0)     html += `<span class="stat synced"><span class="stat-val">${summary.synced_audio}</span> audio-synced</span>`;
    if (summary.synced_timestamp > 0) html += `<span class="stat timestamp"><span class="stat-val">${summary.synced_timestamp}</span> timestamp</span>`;
    if (summary.video_only > 0)       html += `<span class="stat unmatched"><span class="stat-val">${summary.video_only}</span> unmatched</span>`;
    if (summary.audio_only > 0)       html += `<span class="stat"><span class="stat-val">${summary.audio_only}</span> audio-only</span>`;
    dom.resultsSummary.innerHTML = html;

    fetchResults();
}

async function fetchResults() {
    try {
        const res = await fetch('/api/results');
        if (!res.ok) return;
        const data = await res.json();
        state.results = data;
        dom.resultsList.innerHTML = '';
        (data.serialized || []).forEach(addResultRow);
    } catch (e) { console.error('[SyncLab] fetchResults:', e); }
}

function addResultRow(d) {
    const el = document.createElement('div');
    const type = d.type || 'unknown';
    let icon, cls, badge, badgeCls;
    let vname = d.video_name || d.video || '\u2014';
    let aname = d.audio_name || d.audio || '\u2014';
    let conf = Math.min(d.confidence || 0, 1.0);
    let off = d.offset || 0;

    if (type === 'synced' && d.method === 'timestamp_only') {
        icon = '\u{1F552}'; cls = 'ts-only'; badge = 'TIMESTAMP'; badgeCls = 'ts';
    } else if (type === 'synced') {
        icon = '\u2705'; cls = 'synced'; badge = 'AUDIO'; badgeCls = 'audio';
    } else if (type === 'video_only') {
        icon = '\u274C'; cls = 'no-match'; badge = 'NO MATCH'; badgeCls = 'none'; aname = 'No audio found';
    } else if (type === 'audio_only') {
        icon = '\u{1F3B5}'; cls = 'audio-only'; badge = 'AUDIO ONLY'; badgeCls = 'none'; vname = aname; aname = 'No video matched';
    } else {
        icon = '\u2753'; cls = ''; badge = type; badgeCls = '';
    }

    // Confidence badge with colored dot (v1.1)
    const serverBadge = d.badge || '';
    let confDotCls;
    if (serverBadge === 'high') {
        confDotCls = 'conf-high';
    } else if (serverBadge === 'medium') {
        confDotCls = 'conf-med';
    } else if (serverBadge === 'low') {
        confDotCls = 'conf-low';
    } else {
        // Fallback to local computation
        confDotCls = conf >= 0.30 ? 'conf-high' : conf >= 0.10 ? 'conf-med' : 'conf-low';
    }

    const confPct = Math.round(conf * 100);
    const offStr = off !== 0 ? ` \u00B7 ${off >= 0 ? '+' : ''}${off.toFixed(2)}s` : '';
    const showConf = type !== 'video_only' && type !== 'audio_only';

    el.className = `result-row ${cls}`;
    el.innerHTML = `
        <div class="result-icon">${icon}</div>
        <div class="result-info">
            <div class="result-name">${esc(vname)}</div>
            <div class="result-sub">${esc(aname)}${offStr}</div>
        </div>
        <span class="result-badge ${badgeCls}">${badge}</span>
        <div class="result-conf ${confDotCls}">
            ${showConf ? '<span class="conf-dot"></span>' : ''}
            <div class="conf-val">${showConf ? confPct + '%' : '\u2014'}</div>
            <div class="conf-label">${showConf ? 'conf' : ''}</div>
        </div>`;

    dom.resultsList.appendChild(el);
    dom.resultsList.scrollTop = dom.resultsList.scrollHeight;
}


/* ============================================================
   Export — PyWebView native or Flask fallback
   ============================================================ */
async function exportXML() {
    try {
        dom.exportBtn.disabled = true;
        dom.exportBtn.textContent = 'Choose folder...';

        // Always use Flask /api/browse to avoid PyWebView API deadlocks
        const browseRes = await fetch('/api/browse', { method: 'POST' });
        if (!browseRes.ok) throw new Error('Could not open folder dialog');
        const browseData = await browseRes.json();
        const outputDir = browseData.path;

        if (!outputDir) {
            toast('info', 'Export cancelled');
            return;
        }

        dom.exportBtn.textContent = 'Exporting...';
        const res = await fetch('/api/export', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ project_name: 'SyncLab', output_dir: outputDir }),
        });
        if (!res.ok) throw new Error((await res.json()).error || 'Export failed');
        const data = await res.json();
        state.lastExportPath = data.xml_path;
        toast('success', `XML exported: ${data.filename}`);
        showOpenFolderBtn(data.xml_path);
    } catch (e) { toast('error', e.message); }
    finally {
        dom.exportBtn.disabled = false;
        dom.exportBtn.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg> Export XML';
    }
}

function showOpenFolderBtn(xmlPath) {
    const existing = document.getElementById('openFolderBtn');
    if (existing) existing.remove();

    const btn = document.createElement('button');
    btn.id = 'openFolderBtn';
    btn.className = 'btn-action';
    btn.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"/></svg> Open XML in Explorer';
    btn.title = xmlPath;
    btn.addEventListener('click', async () => {
        try {
            // Always use Flask endpoint to avoid PyWebView API deadlocks
            await fetch('/api/open_folder', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path: xmlPath }),
            });
        } catch (e) { toast('error', 'Could not open folder'); }
    });

    dom.exportBtn.parentNode.insertBefore(btn, dom.exportBtn.nextSibling);
}


/* ============================================================
   Settings
   ============================================================ */
async function loadConfig() {
    try {
        const res = await fetch('/api/config');
        if (!res.ok) return;
        state.config = await res.json();
        fillSettings(state.config);
    } catch (e) { console.error('[SyncLab] loadConfig:', e); }
}

function fillSettings(c) {
    dom.settingFps.value = c.premiere_fps || 29.97;
    dom.settingWidth.value = c.premiere_width || 1920;
    dom.settingHeight.value = c.premiere_height || 1080;
    dom.settingThreshold.value = c.threshold || 0.05;
    dom.settingVideoExt.value = (c.video_extensions || []).join(', ');
    dom.settingAudioExt.value = (c.audio_extensions || []).join(', ');
}

async function saveConfig() {
    const c = {
        premiere_fps: parseFloat(dom.settingFps.value) || 29.97,
        premiere_width: parseInt(dom.settingWidth.value) || 1920,
        premiere_height: parseInt(dom.settingHeight.value) || 1080,
        threshold: parseFloat(dom.settingThreshold.value) || 0.05,
        video_extensions: dom.settingVideoExt.value.split(',').map(s => s.trim()).filter(Boolean),
        audio_extensions: dom.settingAudioExt.value.split(',').map(s => s.trim()).filter(Boolean),
    };
    try {
        const res = await fetch('/api/config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(c) });
        if (res.ok) { state.config = await res.json(); closeModal(); toast('success', 'Settings saved'); }
    } catch (e) { toast('error', 'Failed to save settings'); }
}

function openModal()  { dom.settingsModal.style.display = 'flex'; }
function closeModal() { dom.settingsModal.style.display = 'none'; }


/* ============================================================
   Reset
   ============================================================ */
function resetAll() {
    state.videoFolders = []; state.audioFolders = [];
    state.results = null; state.syncing = false;
    _lastVi = -1;
    clearInterval(_stepFillTimer);
    dom.videoPathInput.dataset.paths = '[]';
    dom.videoPathInput.innerHTML = '<span class="path-placeholder">Click Browse Folder or drag here</span>';
    dom.audioPathInput.dataset.paths = '[]';
    dom.audioPathInput.innerHTML = '<span class="path-placeholder">Click Browse Folder or drag here</span>';
    persistPaths();
    dom.videoCount.textContent = ''; dom.audioCount.textContent = '';
    dom.videoCount.classList.remove('scanning');
    dom.audioCount.classList.remove('scanning');
    dom.syncControls.style.display = 'block';
    dom.progressSection.style.display = 'none';
    dom.resultsSection.style.display = 'none';
    dom.inputSection.style.display = 'block';
    dom.resultsList.innerHTML = '';
    dom.stepBar.classList.remove('step-indeterminate');
    resetSyncBtn();
}


/* ============================================================
   Toast
   ============================================================ */
function toast(type, msg, ms = 4000) {
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    const icons = { success: '\u2705', error: '\u274C', warning: '\u26A0\uFE0F', info: '\u2139\uFE0F' };
    el.innerHTML = `<span class="toast-icon">${icons[type] || icons.info}</span><span>${esc(msg)}</span>`;
    dom.toastContainer.appendChild(el);
    setTimeout(() => { el.classList.add('toast-exit'); setTimeout(() => el.remove(), 200); }, ms);
}


/* ============================================================
   Completion Notification Sound
   ============================================================ */
function playCompletionChime() {
    try {
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        const now = ctx.currentTime;
        // Three-note ascending chime (C5, E5, G5)
        const notes = [523.25, 659.25, 783.99];
        notes.forEach((freq, i) => {
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.type = 'sine';
            osc.frequency.value = freq;
            gain.gain.setValueAtTime(0, now + i * 0.15);
            gain.gain.linearRampToValueAtTime(0.18, now + i * 0.15 + 0.03);
            gain.gain.exponentialRampToValueAtTime(0.001, now + i * 0.15 + 0.5);
            osc.connect(gain);
            gain.connect(ctx.destination);
            osc.start(now + i * 0.15);
            osc.stop(now + i * 0.15 + 0.5);
        });
        // Close context after sound finishes
        setTimeout(() => ctx.close().catch(() => {}), 1200);
    } catch (e) {
        console.log('[SyncLab] Audio notification not available:', e);
    }
}


/* ============================================================
   Helpers
   ============================================================ */
function esc(t) { const d = document.createElement('div'); d.textContent = t; return d.innerHTML; }
function clamp(v) { return Math.min(100, Math.max(0, Math.round(v))); }


/* ============================================================
   Dependency Warning
   ============================================================ */
function showDependencyWarning(msg) {
    const banner = document.createElement('div');
    banner.className = 'dep-warning';
    banner.innerHTML = `
        <span class="dep-warning-icon">\u26A0\uFE0F</span>
        <span class="dep-warning-msg">${esc(msg).replace(
            'https://ffmpeg.org/download.html',
            '<a href="https://ffmpeg.org/download.html" target="_blank">ffmpeg.org/download</a>'
        )}</span>
        <button class="dep-warning-close" title="Dismiss">\u2715</button>`;
    banner.querySelector('.dep-warning-close').addEventListener('click', () => banner.remove());
    document.body.insertBefore(banner, document.body.firstChild);
}


/* ============================================================
   Export Support Package
   ============================================================ */
async function exportSupportPackage() {
    try {
        dom.supportExportBtn.disabled = true;
        dom.supportExportBtn.textContent = 'Choose folder...';

        const browseRes = await fetch('/api/browse', { method: 'POST' });
        if (!browseRes.ok) throw new Error('Could not open folder dialog');
        const browseData = await browseRes.json();
        const outputDir = browseData.path;

        if (!outputDir) {
            toast('info', 'Export cancelled');
            return;
        }

        dom.supportExportBtn.textContent = 'Exporting...';
        const res = await fetch('/api/export-support', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ output_dir: outputDir }),
        });
        if (!res.ok) throw new Error((await res.json()).error || 'Export failed');
        const data = await res.json();
        toast('success', `Support package saved: ${data.filename}`);
    } catch (e) { toast('error', e.message); }
    finally {
        dom.supportExportBtn.disabled = false;
        dom.supportExportBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg> Export Support Package';
    }
}


/* ============================================================
   Event Listeners
   ============================================================ */
setupCardDrop(dom.videoCard, dom.videoPathInput);
setupCardDrop(dom.audioCard, dom.audioPathInput);

document.querySelectorAll('.btn-browse').forEach(btn => {
    btn.addEventListener('click', () => browseFolder(btn.dataset.target, btn));
});

dom.syncBtn.addEventListener('click', startSync);
dom.cancelBtn.addEventListener('click', () => { socket.emit('cancel_sync'); toast('warning', 'Cancelling...'); });

dom.exportBtn.addEventListener('click', exportXML);
dom.supportExportBtn.addEventListener('click', exportSupportPackage);
dom.newSyncBtn.addEventListener('click', resetAll);

dom.settingsBtn.addEventListener('click', openModal);
dom.closeSettings.addEventListener('click', closeModal);
dom.saveSettings.addEventListener('click', saveConfig);
dom.resetSettings.addEventListener('click', () => fillSettings({ premiere_fps: 29.97, premiere_width: 1920, premiere_height: 1080, threshold: 0.05, video_extensions: ['.mov', '.mp4', '.mxf', '.avi'], audio_extensions: ['.wav'] }));
dom.settingsModal.addEventListener('click', (e) => { if (e.target === dom.settingsModal) closeModal(); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });


/* ============================================================
   Init
   ============================================================ */
(async () => {
    await loadConfig();

    // Fetch version and update badges
    fetch('/api/version')
        .then(r => r.json())
        .then(d => {
            if (d.version) {
                const b = document.getElementById('versionBadge');
                if (b) b.textContent = 'v' + d.version;
                const sv = document.getElementById('splashVersion');
                if (sv) sv.textContent = 'v' + d.version;
            }
        })
        .catch(() => {});

    // Check FFmpeg dependency
    fetch('/api/check-dependencies')
        .then(r => r.json())
        .then(d => { if (!d.all_ok) showDependencyWarning(d.message); })
        .catch(() => {});

    // Restore remembered folders (multi-folder with migration from single)
    const savedVideoDirs = state.config.last_video_dirs ||
        (state.config.last_video_dir ? [state.config.last_video_dir] : []);
    const savedAudioDirs = state.config.last_audio_dirs ||
        (state.config.last_audio_dir ? [state.config.last_audio_dir] : []);

    if (savedVideoDirs.length > 0) {
        setPaths(dom.videoPathInput, savedVideoDirs);
    }
    if (savedAudioDirs.length > 0) {
        setPaths(dom.audioPathInput, savedAudioDirs);
    }
    // Ensure state and sync button are up to date
    onPathChange();

    const hint = document.querySelector('.section-hint');
    const pywebReady = await waitForPyWebView(2000);
    if (pywebReady && hint) {
        hint.textContent = 'Drag folders here or use Browse Folder';
        console.log('[SyncLab] PyWebView detected \u2014 native drag-and-drop enabled');
    } else if (hint) {
        hint.textContent = 'Use Browse Folder to select directories';
        console.log('[SyncLab] Browser mode \u2014 using Browse Folder dialogs');
    }

    // Hide splash screen (minimum 3s display time)
    const splash = document.getElementById('splashScreen');
    if (splash) {
        const elapsed = Date.now() - _splashStart;
        const remaining = Math.max(0, 3000 - elapsed);
        setTimeout(() => {
            splash.classList.add('fade-out');
            setTimeout(() => splash.remove(), 600);
        }, remaining);
    }

    console.log('[SyncLab] Ready');
})();
