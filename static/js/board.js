'use strict';

// ── State ────────────────────────────────────────────────────────────────
const State = {
    cards: [...(window.INITIAL_CARDS || [])],
    activeCardId: null,
};

function cardById(id) {
    return State.cards.find(c => c.id === id) || null;
}

function updateLocalCard(updated) {
    const idx = State.cards.findIndex(c => c.id === updated.id);
    if (idx !== -1) State.cards[idx] = updated;
    else State.cards.push(updated);
}

function removeLocalCard(id) {
    State.cards = State.cards.filter(c => c.id !== id);
}

// ── Theme ────────────────────────────────────────────────────────────────
function initTheme() {
    const saved = localStorage.getItem('sdm-kanban-theme') || 'dark';
    document.documentElement.setAttribute('data-theme', saved);
    updateThemeLabel(saved);
}

function toggleTheme() {
    const current = document.documentElement.getAttribute('data-theme') || 'dark';
    const next = current === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('sdm-kanban-theme', next);
    updateThemeLabel(next);
}

function updateThemeLabel(theme) {
    const btn = document.getElementById('theme-toggle-btn');
    if (btn) btn.textContent = theme === 'dark' ? '☀ Light' : '☾ Dark';
}

// ── Toast ────────────────────────────────────────────────────────────────
function toast(msg, type = 'success') {
    const container = document.getElementById('toast-container');
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.textContent = msg;
    container.appendChild(el);
    setTimeout(() => el.remove(), 3200);
}

// ── API helpers ──────────────────────────────────────────────────────────
async function apiFetch(path, method = 'GET', body = null) {
    const opts = { method, headers: { 'Content-Type': 'application/json' } };
    if (body) opts.body = JSON.stringify(body);
    const res = await fetch(path, opts);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    return data;
}

// ── Render board ─────────────────────────────────────────────────────────
const BUCKET_LABELS = {
    'ideas': 'Ideas',
    'in-progress': 'In Progress',
    'shared-progress': 'Shared Progress',
    'complete': 'Complete',
};

function renderBoard() {
    const buckets = { 'ideas': [], 'in-progress': [], 'shared-progress': [], 'complete': [] };
    for (const card of State.cards) {
        const b = card.bucket || 'ideas';
        if (buckets[b]) buckets[b].push(card);
    }
    for (const b of Object.keys(buckets)) {
        buckets[b].sort((a, c) => (a.priority ?? 999) - (c.priority ?? 999));
    }

    for (const [bucket, cards] of Object.entries(buckets)) {
        const list = document.getElementById(`list-${bucket}`);
        const countEl = document.getElementById(`count-${bucket}`);
        if (!list) continue;

        list.innerHTML = '';
        if (countEl) countEl.textContent = cards.length;

        for (let i = 0; i < cards.length; i++) {
            list.appendChild(buildCardEl(cards[i], bucket, i + 1));
        }
    }

    initSortable();
    if (_searchQuery) applySearch();
}

function buildCardEl(card, bucket, rank) {
    const el = document.createElement('div');
    el.className = 'kanban-card';
    el.dataset.id = card.id;

    const title = document.createElement('div');
    title.className = 'card-title-text';
    title.textContent = card.title;
    el.appendChild(title);

    const meta = document.createElement('div');
    meta.className = 'card-meta';

    if (bucket === 'ideas') {
        const badge = document.createElement('span');
        badge.className = 'priority-badge';
        badge.textContent = `#${rank}`;
        meta.appendChild(badge);
    }

    const createdBy = card.created_by || 'kamancha';
    const cb = document.createElement('span');
    cb.className = 'created-by-badge';
    cb.textContent = createdBy;
    meta.appendChild(cb);

    if (bucket === 'shared-progress' && card.cec_ids && card.cec_ids.length > 0) {
        const cecWrap = document.createElement('div');
        cecWrap.className = 'cec-badges';
        for (const cid of card.cec_ids) {
            const badge = document.createElement('span');
            badge.className = 'cec-badge';
            badge.textContent = cid;
            cecWrap.appendChild(badge);
        }
        meta.appendChild(cecWrap);
    }

    el.appendChild(meta);
    el.addEventListener('click', () => openCardModal(card.id));
    return el;
}

// ── SortableJS ───────────────────────────────────────────────────────────
let _sortables = [];

function initSortable() {
    _sortables.forEach(s => s.destroy());
    _sortables = [];

    const buckets = ['ideas', 'in-progress', 'shared-progress', 'complete'];
    for (const bucket of buckets) {
        const list = document.getElementById(`list-${bucket}`);
        if (!list) continue;

        const s = Sortable.create(list, {
            group: { name: 'kanban', pull: true, put: true },
            sort: true,
            animation: 150,
            ghostClass: 'sortable-ghost',
            chosenClass: 'sortable-chosen',
            onEnd: handleSortEnd,
        });
        _sortables.push(s);
    }
}

async function handleSortEnd(evt) {
    const cardId = evt.item.dataset.id;
    if (!cardId) return;

    const fromBucket = evt.from.dataset.bucket;
    const toBucket = evt.to.dataset.bucket;

    if (!fromBucket || !toBucket) return;

    if (fromBucket === toBucket) {
        const list = document.getElementById(`list-${fromBucket}`);
        const cardIds = [...list.querySelectorAll('.kanban-card')].map(el => el.dataset.id);
        try {
            await apiFetch('/api/cards/reorder-bulk', 'POST', { card_ids: cardIds });
            cardIds.forEach((id, i) => {
                const c = cardById(id);
                if (c) c.priority = i;
            });
            refreshCounts();
        } catch (e) {
            toast(`Reorder failed: ${e.message}`, 'error');
            renderBoard();
        }
        return;
    }

    // Cross-column move
    try {
        const result = await apiFetch(`/api/cards/${cardId}/move`, 'PUT', { bucket: toBucket });
        updateLocalCard(result.card);
        renderBoard();
        if (toBucket === 'shared-progress') {
            openCardModal(cardId);
        }
        toast(`Moved to ${BUCKET_LABELS[toBucket]}`);
    } catch (e) {
        toast(`Move failed: ${e.message}`, 'error');
        renderBoard();
    }
}

function refreshCounts() {
    const buckets = { 'ideas': 0, 'in-progress': 0, 'shared-progress': 0, 'complete': 0 };
    for (const card of State.cards) {
        if (buckets[card.bucket] !== undefined) buckets[card.bucket]++;
    }
    for (const [bucket, count] of Object.entries(buckets)) {
        const el = document.getElementById(`count-${bucket}`);
        if (el) el.textContent = count;
    }
}

// ── Add Card Modal ───────────────────────────────────────────────────────
function openAddModal() {
    document.getElementById('new-title').value = '';
    document.getElementById('new-desc').value = '';
    document.getElementById('add-modal').style.display = 'flex';
    setTimeout(() => document.getElementById('new-title').focus(), 50);
}

function closeAddModal() {
    document.getElementById('add-modal').style.display = 'none';
}

async function submitNewCard(e) {
    e.preventDefault();
    const title = document.getElementById('new-title').value.trim();
    const desc = document.getElementById('new-desc').value.trim();
    if (!title) return;

    try {
        const result = await apiFetch('/api/cards', 'POST', { title, description: desc, bucket: 'ideas' });
        updateLocalCard(result.card);
        renderBoard();
        closeAddModal();
        toast('Card added to Ideas');
    } catch (e) {
        toast(`Error: ${e.message}`, 'error');
    }
}

// ── Card Detail Modal ────────────────────────────────────────────────────
function openCardModal(cardId) {
    const card = cardById(cardId);
    if (!card) return;
    State.activeCardId = cardId;

    const modal = document.getElementById('card-modal');
    const box = modal.querySelector('.modal-box');

    const bucketClass = card.bucket.replace('-', '-');
    const isShared = card.bucket === 'shared-progress';

    box.innerHTML = `
        <button class="modal-close" onclick="closeCardModal()">×</button>
        <div style="margin-bottom:1rem; display:flex; align-items:center; gap:0.6rem;">
            <span class="bucket-badge ${card.bucket}">${BUCKET_LABELS[card.bucket] || card.bucket}</span>
        </div>
        <div class="form-group">
            <label>Title</label>
            <input type="text" id="modal-title" class="form-input" value="${escHtml(card.title)}" maxlength="200">
        </div>
        <div class="form-group">
            <label>Description</label>
            <textarea id="modal-desc" class="form-textarea" rows="5">${escHtml(card.description || '')}</textarea>
        </div>
        ${isShared ? renderCecSection(card) : ''}
        <div class="card-timestamps">
            Created by: <strong>${escHtml(card.created_by || 'kamancha')}</strong>
            &nbsp;·&nbsp; ${fmtDate(card.created_at)}
            &nbsp;·&nbsp; Updated: ${fmtDate(card.updated_at)}
        </div>
        <div class="form-actions">
            <button class="btn btn-danger btn-sm" onclick="deleteCard('${card.id}')">Delete</button>
            <button class="btn btn-secondary" onclick="closeCardModal()">Cancel</button>
            <button class="btn btn-primary" onclick="saveCard('${card.id}')">Save</button>
        </div>
    `;

    modal.style.display = 'flex';
    document.getElementById('modal-title').focus();
}

function renderCecSection(card) {
    const tags = (card.cec_ids || []).map(cid =>
        `<span class="cec-tag">${escHtml(cid)}<button onclick="removeCec('${card.id}','${cid}')" title="Remove">×</button></span>`
    ).join('');

    return `
        <div class="form-group">
            <label>Team Members (CEC-IDs)</label>
            <div class="cec-section">
                <div class="cec-list" id="cec-list">${tags || '<span style="color:var(--text-secondary);font-size:0.8rem">None yet</span>'}</div>
                <div class="cec-add-row">
                    <input type="text" id="cec-input" class="form-input" placeholder="e.g. kamancha" maxlength="40">
                    <button class="btn btn-secondary btn-sm" onclick="addCec('${card.id}')">Add</button>
                </div>
            </div>
        </div>
    `;
}

function closeCardModal() {
    document.getElementById('card-modal').style.display = 'none';
    State.activeCardId = null;
}

async function saveCard(cardId) {
    const title = (document.getElementById('modal-title')?.value || '').trim();
    const desc = (document.getElementById('modal-desc')?.value || '').trim();
    if (!title) { toast('Title cannot be empty', 'error'); return; }

    try {
        const result = await apiFetch(`/api/cards/${cardId}`, 'PUT', { title, description: desc });
        updateLocalCard(result.card);
        renderBoard();
        closeCardModal();
        toast('Card saved');
    } catch (e) {
        toast(`Save failed: ${e.message}`, 'error');
    }
}

async function deleteCard(cardId) {
    if (!confirm('Delete this card?')) return;
    try {
        await apiFetch(`/api/cards/${cardId}`, 'DELETE');
        removeLocalCard(cardId);
        closeCardModal();
        renderBoard();
        toast('Card deleted');
    } catch (e) {
        toast(`Delete failed: ${e.message}`, 'error');
    }
}

async function addCec(cardId) {
    const input = document.getElementById('cec-input');
    const cec_id = (input?.value || '').trim().toLowerCase();
    if (!cec_id) return;

    try {
        const result = await apiFetch(`/api/cards/${cardId}/cec`, 'POST', { cec_id });
        updateLocalCard(result.card);
        input.value = '';
        // Refresh modal cec list without closing
        const cecList = document.getElementById('cec-list');
        if (cecList) {
            cecList.innerHTML = result.card.cec_ids.map(cid =>
                `<span class="cec-tag">${escHtml(cid)}<button onclick="removeCec('${cardId}','${cid}')" title="Remove">×</button></span>`
            ).join('') || '<span style="color:var(--text-secondary);font-size:0.8rem">None yet</span>';
        }
        renderBoard();
        toast(`Added ${cec_id}`);
    } catch (e) {
        toast(`Error: ${e.message}`, 'error');
    }
}

async function removeCec(cardId, cec_id) {
    try {
        const result = await apiFetch(`/api/cards/${cardId}/cec/${encodeURIComponent(cec_id)}`, 'DELETE');
        updateLocalCard(result.card);
        const cecList = document.getElementById('cec-list');
        if (cecList) {
            cecList.innerHTML = result.card.cec_ids.map(cid =>
                `<span class="cec-tag">${escHtml(cid)}<button onclick="removeCec('${cardId}','${cid}')" title="Remove">×</button></span>`
            ).join('') || '<span style="color:var(--text-secondary);font-size:0.8rem">None yet</span>';
        }
        renderBoard();
        toast(`Removed ${cec_id}`);
    } catch (e) {
        toast(`Error: ${e.message}`, 'error');
    }
}

// ── Utils ────────────────────────────────────────────────────────────────
function escHtml(str) {
    return String(str || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function fmtDate(iso) {
    if (!iso) return '—';
    try {
        return new Date(iso).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    } catch { return iso; }
}

// ── Search ───────────────────────────────────────────────────────────────
let _searchQuery = '';

function handleSearch(val) {
    _searchQuery = val.trim().toLowerCase();
    document.getElementById('search-clear').style.display = _searchQuery ? 'block' : 'none';
    applySearch();
}

function clearSearch() {
    _searchQuery = '';
    document.getElementById('search-input').value = '';
    document.getElementById('search-clear').style.display = 'none';
    applySearch();
}

function applySearch() {
    const cardEls = document.querySelectorAll('.kanban-card');
    let matchCount = 0;
    const total = cardEls.length;

    cardEls.forEach(el => {
        if (!_searchQuery) {
            el.classList.remove('search-hidden', 'search-match');
            matchCount++;
            return;
        }
        const card = cardById(el.dataset.id);
        if (!card) return;
        const haystack = [
            card.title,
            card.description || '',
            card.created_by || '',
            ...(card.cec_ids || []),
        ].join(' ').toLowerCase();

        if (haystack.includes(_searchQuery)) {
            el.classList.remove('search-hidden');
            el.classList.add('search-match');
            matchCount++;
        } else {
            el.classList.add('search-hidden');
            el.classList.remove('search-match');
        }
    });

    const badge = document.getElementById('search-badge');
    const clearBtn = document.getElementById('search-clear');
    if (_searchQuery) {
        badge.textContent = `${matchCount}/${total}`;
        badge.style.display = 'inline';
        clearBtn.style.display = 'inline';
    } else {
        badge.style.display = 'none';
        clearBtn.style.display = 'none';
    }
}

// ── Keyboard shortcuts ───────────────────────────────────────────────────
document.addEventListener('keydown', (e) => {
    const tag = document.activeElement?.tagName?.toLowerCase();
    const inInput = tag === 'input' || tag === 'textarea' || tag === 'select';
    if (e.key === 'Escape') {
        closeCardModal();
        closeAddModal();
    }
    if (!inInput && e.key === 'n') {
        e.preventDefault();
        openAddModal();
    }
    if (!inInput && e.key === '/') {
        e.preventDefault();
        document.getElementById('search-input')?.focus();
    }
});

// Close modals on backdrop click
document.getElementById('card-modal')?.addEventListener('click', function(e) {
    if (e.target === this) closeCardModal();
});
document.getElementById('add-modal')?.addEventListener('click', function(e) {
    if (e.target === this) closeAddModal();
});

// ── Init ─────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    initTheme();
    renderBoard();
});
