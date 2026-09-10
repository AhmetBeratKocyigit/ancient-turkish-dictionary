const state = {
  all: [],
  filtered: [],
  page: 1,
  pageSize: 24,
  sortAscending: true,
  activeLetter: '',
  favoritesOnly: false,
  favorites: new Set(JSON.parse(localStorage.getItem('dlt-favorites') || '[]'))
};

const $ = (selector) => document.querySelector(selector);
const searchInput = $('#searchInput');
const resultsGrid = $('#resultsGrid');
const pagination = $('#pagination');
const emptyState = $('#emptyState');
const welcomeState = $('#welcomeState');
const loadingState = $('#loadingState');
const filterDrawer = $('#filterDrawer');
const modalBackdrop = $('#modalBackdrop');
const modalContent = $('#modalContent');

function normalize(value) {
  return String(value || '').toLocaleLowerCase('tr-TR').normalize('NFD').replace(/[\u0300-\u036f]/g, '');
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#039;', '"': '&quot;' })[character]);
}

function pageNumber(value) {
  const match = String(value || '').match(/\d+/);
  return match ? Number(match[0]) : Infinity;
}

function searchableText(entry) {
  return normalize([entry.kelime, entry.anlam, entry.ornek_metin, entry.notlar, entry.dil_veya_lehce, entry.kelime_turu, ...(entry.cekimler || [])].join(' '));
}

function populateFilters() {
  const types = [...new Set(state.all.map((entry) => entry.kelime_turu).filter(Boolean))].sort((a, b) => a.localeCompare(b, 'tr'));
  const dialects = [...new Set(state.all.map((entry) => entry.dil_veya_lehce).filter(Boolean))].sort((a, b) => a.localeCompare(b, 'tr'));
  $('#typeFilter').innerHTML += types.map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join('');
  $('#dialectFilter').innerHTML += dialects.map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join('');
}

function buildLetterNav() {
  const letters = [...new Set(state.all.map((entry) => normalize((entry.kelime || '').trim()).charAt(0).toLocaleUpperCase('tr-TR')).filter(Boolean))].sort((a, b) => a.localeCompare(b, 'tr'));
  $('#letterNav').innerHTML = letters.map((letter) => `<button type="button" data-letter="${escapeHtml(letter)}">${escapeHtml(letter)}</button>`).join('');
  $('#letterNav').addEventListener('click', (event) => {
    const button = event.target.closest('button');
    if (!button) return;
    state.activeLetter = state.activeLetter === button.dataset.letter ? '' : button.dataset.letter;
    searchInput.value = '';
    applyFilters();
    searchInput.focus();
  });
}

function updateFilterCount() {
  const count = [$('#typeFilter').value, $('#dialectFilter').value, $('#pageFilter').value, $('#reviewFilter').checked].filter(Boolean).length;
  $('#activeFilterCount').textContent = count;
}

function applyFilters() {
  const query = normalize(searchInput.value.trim());
  const selectedType = $('#typeFilter').value;
  const selectedDialect = $('#dialectFilter').value;
  const selectedPage = $('#pageFilter').value.trim();
  const reviewOnly = $('#reviewFilter').checked;
  const hasFilters = Boolean(query || state.activeLetter || selectedType || selectedDialect || selectedPage || reviewOnly || state.favoritesOnly);
  state.filtered = state.all.filter((entry) => {
    if (query && !searchableText(entry).includes(query)) return false;
    if (state.activeLetter && normalize(entry.kelime).charAt(0).toLocaleUpperCase('tr-TR') !== state.activeLetter) return false;
    if (selectedType && entry.kelime_turu !== selectedType) return false;
    if (selectedDialect && entry.dil_veya_lehce !== selectedDialect) return false;
    if (selectedPage && !String(entry.kaynak_sayfa || '').split('-').some((part) => part === selectedPage)) return false;
    if (reviewOnly && !entry.kontrol_gerekli) return false;
    if (state.favoritesOnly && !state.favorites.has(entryId(entry))) return false;
    return true;
  });
  state.filtered.sort((a, b) => state.sortAscending ? compareEntries(a, b) : compareEntries(b, a));
  state.page = 1;
  updateFilterCount();
  render(hasFilters);
}

function compareEntries(a, b) {
  return String(a.kelime || '').localeCompare(String(b.kelime || ''), 'tr', { sensitivity: 'base' }) || pageNumber(a.kaynak_sayfa) - pageNumber(b.kaynak_sayfa);
}

function entryId(entry) {
  return `${entry.kelime}|${entry.anlam}|${entry.kaynak_sayfa}`;
}

function saveFavorites() {
  localStorage.setItem('dlt-favorites', JSON.stringify([...state.favorites]));
  $('#favoriteCount').textContent = state.favorites.size;
}

function cardTemplate(entry, index) {
  const favorite = state.favorites.has(entryId(entry));
  const example = entry.ornek_metin || entry.notlar || '';
  return `<article class="entry-card" data-index="${index}" style="animation-delay:${Math.min(index * 18, 180)}ms">
    <div class="entry-top"><div><h2 class="entry-word">${escapeHtml(entry.kelime || '—')}</h2><div class="entry-type">${escapeHtml(entry.kelime_turu || 'belirsiz')}</div></div>
    <button class="favorite ${favorite ? 'is-favorite' : ''}" type="button" data-favorite="${escapeHtml(entryId(entry))}" aria-label="${favorite ? 'Favoriden çıkar' : 'Favoriye ekle'}">${favorite ? '★' : '☆'}</button></div>
    <p class="entry-meaning">${escapeHtml(entry.anlam || 'Anlam belirtilmemiş')}</p>
    ${example ? `<p class="entry-example">${escapeHtml(example)}</p>` : '<p class="entry-example"></p>'}
    <div class="entry-footer"><span>sf. ${escapeHtml(entry.kaynak_sayfa || '—')}</span><span>${entry.kontrol_gerekli ? '<b class="review-dot">● kontrol</b>' : escapeHtml(entry.dil_veya_lehce || '')}</span></div>
  </article>`;
}

function render(hasFilters = false) {
  const start = (state.page - 1) * state.pageSize;
  const visible = state.filtered.slice(start, start + state.pageSize);
  $('#resultSummary').textContent = hasFilters ? `${state.filtered.length.toLocaleString('tr-TR')} sonuç · ${state.all.length.toLocaleString('tr-TR')} toplam kayıt` : 'Arama veya harf seçimi bekleniyor';
  resultsGrid.innerHTML = visible.map((entry, index) => cardTemplate(entry, index)).join('');
  resultsGrid.hidden = !hasFilters || visible.length === 0;
  welcomeState.hidden = hasFilters;
  emptyState.hidden = !hasFilters || visible.length !== 0;
  pagination.hidden = !hasFilters;
  pagination.innerHTML = hasFilters ? paginationTemplate() : '';
  document.querySelectorAll('#letterNav button').forEach((button) => button.classList.toggle('active', button.dataset.letter === state.activeLetter));
}

function paginationTemplate() {
  const totalPages = Math.ceil(state.filtered.length / state.pageSize);
  if (totalPages <= 1) return '';
  const pages = new Set([1, totalPages, state.page, state.page - 1, state.page + 1].filter((page) => page > 0 && page <= totalPages));
  const sorted = [...pages].sort((a, b) => a - b);
  let html = `<button type="button" data-page="${state.page - 1}" ${state.page === 1 ? 'disabled' : ''}>‹</button>`;
  let previous = 0;
  sorted.forEach((page) => {
    if (page - previous > 1) html += '<span class="pagination-gap">…</span>';
    html += `<button type="button" data-page="${page}" class="${page === state.page ? 'current' : ''}">${page}</button>`;
    previous = page;
  });
  return `${html}<button type="button" data-page="${state.page + 1}" ${state.page === totalPages ? 'disabled' : ''}>›</button>`;
}

function openDetail(entry) {
  const forms = (entry.cekimler || []).join(', ');
  modalContent.innerHTML = `<p class="eyebrow"><span></span> Sözlük maddesi</p><h2 id="modalWord" class="modal-word">${escapeHtml(entry.kelime || '—')}</h2><span class="modal-type">${escapeHtml(entry.kelime_turu || 'belirsiz')}</span><p class="detail-meaning">${escapeHtml(entry.anlam || 'Anlam belirtilmemiş')}</p>
    ${entry.ornek_metin ? `<div class="detail-section"><span class="detail-label">Örnek / kullanım</span><p class="detail-value">${escapeHtml(entry.ornek_metin)}</p></div>` : ''}
    ${forms ? `<div class="detail-section"><span class="detail-label">Çekimler</span><p class="detail-value">${escapeHtml(forms)}</p></div>` : ''}
    ${entry.notlar ? `<div class="detail-section"><span class="detail-label">Notlar</span><p class="detail-value">${escapeHtml(entry.notlar)}</p></div>` : ''}
    <div class="detail-section detail-meta"><div><span class="detail-label">Kaynak sayfa</span><p class="detail-value">${escapeHtml(entry.kaynak_sayfa || '—')}</p></div><div><span class="detail-label">Dil / lehçe</span><p class="detail-value">${escapeHtml(entry.dil_veya_lehce || 'Belirtilmemiş')}</p></div></div>`;
  modalBackdrop.hidden = false;
  document.body.classList.add('modal-open');
}

function closeModal(backdrop) { backdrop.hidden = true; if ($('#modalBackdrop').hidden && $('#aboutBackdrop').hidden) document.body.classList.remove('modal-open'); }

resultsGrid.addEventListener('click', (event) => {
  const favoriteButton = event.target.closest('[data-favorite]');
  const card = event.target.closest('.entry-card');
  if (favoriteButton) {
    event.stopPropagation();
    const id = favoriteButton.dataset.favorite;
    state.favorites.has(id) ? state.favorites.delete(id) : state.favorites.add(id);
    saveFavorites();
    applyFilters();
    return;
  }
  if (card) openDetail(state.filtered[(state.page - 1) * state.pageSize + Number(card.dataset.index)]);
});

pagination.addEventListener('click', (event) => { const button = event.target.closest('[data-page]'); if (button && !button.disabled) { state.page = Number(button.dataset.page); render(); window.scrollTo({ top: $('.content-grid').offsetTop - 90, behavior: 'smooth' }); } });
searchInput.addEventListener('input', () => { state.activeLetter = ''; applyFilters(); });
['typeFilter', 'dialectFilter', 'pageFilter', 'reviewFilter'].forEach((id) => $(`#${id}`).addEventListener('input', applyFilters));
$('#filterToggle').addEventListener('click', () => { const isHidden = filterDrawer.hidden; filterDrawer.hidden = !isHidden; $('#filterToggle').setAttribute('aria-expanded', String(isHidden)); });
$('#clearFilters').addEventListener('click', () => { searchInput.value = ''; state.activeLetter = ''; $('#typeFilter').value = ''; $('#dialectFilter').value = ''; $('#pageFilter').value = ''; $('#reviewFilter').checked = false; applyFilters(); });
$('#sortButton').addEventListener('click', () => { state.sortAscending = !state.sortAscending; $('#sortButton').firstChild.textContent = state.sortAscending ? 'A–Z ' : 'Z–A '; applyFilters(); });
$('#favoritesToggle').addEventListener('click', () => { state.favoritesOnly = !state.favoritesOnly; $('#favoritesToggle').classList.toggle('is-active', state.favoritesOnly); applyFilters(); });
$('#modalClose').addEventListener('click', () => closeModal(modalBackdrop));
$('#aboutButton').addEventListener('click', () => { $('#aboutBackdrop').hidden = false; document.body.classList.add('modal-open'); });
$('#aboutClose').addEventListener('click', () => closeModal($('#aboutBackdrop')));
[modalBackdrop, $('#aboutBackdrop')].forEach((backdrop) => backdrop.addEventListener('click', (event) => { if (event.target === backdrop) closeModal(backdrop); }));
document.addEventListener('keydown', (event) => { if (event.key === '/' && document.activeElement !== searchInput) { event.preventDefault(); searchInput.focus(); } if (event.key === 'Escape') { closeModal(modalBackdrop); closeModal($('#aboutBackdrop')); } });
document.querySelectorAll('[data-query]').forEach((button) => button.addEventListener('click', () => { searchInput.value = button.dataset.query; applyFilters(); searchInput.focus(); }));

async function init() {
  try {
    const response = await fetch('data.json');
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.all = await response.json();
    state.filtered = [];
    $('#heroTotal').textContent = state.all.length.toLocaleString('tr-TR');
    $('#aboutTotal').textContent = state.all.length.toLocaleString('tr-TR');
    $('#aboutPages').textContent = new Set(state.all.flatMap((entry) => String(entry.kaynak_sayfa || '').split('-'))).size.toLocaleString('tr-TR');
    $('#favoriteCount').textContent = state.favorites.size;
    populateFilters();
    buildLetterNav();
    loadingState.hidden = true;
    updateFilterCount();
    render(false);
  } catch (error) {
    loadingState.innerHTML = '<span class="empty-glyph">!</span><h2>Veri yüklenemedi</h2><p>Siteyi yerel bir sunucu üzerinden açın. Örn: <code>python -m http.server</code></p>';
    console.error(error);
  }
}

init();
