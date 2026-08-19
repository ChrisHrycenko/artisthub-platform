/**
 * browse-releases.js
 *
 * Drives the browse-releases.html page.
 *
 * Responsibilities:
 *   1. On load — fetch GET /api/releases and render release cards.
 *   2. Genre filter — pass ?genre= to the API for server-side filtering.
 *   3. Title search — debounced client-side filter on the current page.
 *   4. Release-type chips — client-side filter by Single/EP/Album/etc.
 *   5. Pagination — Previous / Next controls.
 *
 * All HTTP calls go through window.api (api.js).
 * All API-supplied text is escaped before insertion into innerHTML.
 */

// ------------------------------------------------------------------ //
// Constants                                                            //
// ------------------------------------------------------------------ //

const RELEASE_TYPES = [
  "Single", "EP", "Album", "Mixtape", "Compilation", "Live",
];

const PER_PAGE = 20;

// ------------------------------------------------------------------ //
// State                                                                //
// ------------------------------------------------------------------ //

let currentPage     = 1;
let activeTypeFilter = null;   // null = all types
let allReleases     = [];      // current page, unfiltered by search

// ------------------------------------------------------------------ //
// Bootstrap                                                            //
// ------------------------------------------------------------------ //

document.addEventListener('DOMContentLoaded', () => {
  renderTypeChips();
  loadPage(1);
  bindSearch();
  bindGenreSelect();
});

// ------------------------------------------------------------------ //
// Data loading                                                         //
// ------------------------------------------------------------------ //

/**
 * Fetch a page of releases from the API and render the grid.
 *
 * @param {number} page - 1-based page number.
 */
async function loadPage(page) {
  const grid    = document.getElementById('releases-grid');
  const summary = document.getElementById('results-summary');

  grid.innerHTML = `
    <div class="card placeholder">
      <div class="placeholder-img"></div>
      <p class="placeholder-text">Loading…</p>
    </div>
  `;

  // Build query string — include genre if selected.
  const genre = document.getElementById('genre-select')?.value || '';
  const params = new URLSearchParams({
    page,
    per_page: PER_PAGE,
    ...(genre ? { genre } : {}),
  });

  try {
    const data = await api.get(`/releases?${params}`);
    allReleases  = data.releases || [];
    currentPage  = data.page;

    summary.textContent = data.total === 0
      ? 'No releases found.'
      : `Showing ${allReleases.length} of ${data.total} release${data.total === 1 ? '' : 's'}`;

    applyFiltersAndRender();
    renderPagination(data);
  } catch (err) {
    grid.innerHTML = `
      <div class="alert alert-error">
        Could not load releases: ${escapeHtml(err.message)}
      </div>
    `;
    summary.textContent = '';
  }
}

// ------------------------------------------------------------------ //
// Rendering                                                            //
// ------------------------------------------------------------------ //

/**
 * Apply the active type chip filter and title search, then render.
 * Called whenever either filter changes so state is not duplicated.
 */
function applyFiltersAndRender() {
  const search = (
    document.getElementById('search-input')?.value || ''
  ).trim().toLowerCase();

  let filtered = allReleases;

  // Filter by release type chip.
  if (activeTypeFilter) {
    filtered = filtered.filter(
      r => r.release_type === activeTypeFilter
    );
  }

  // Filter by title search text.
  if (search) {
    filtered = filtered.filter(
      r => r.title.toLowerCase().includes(search)
    );
  }

  renderGrid(filtered);
}

/**
 * Render release cards into the grid.
 *
 * @param {Array} releases - Array of release objects from the API.
 */
function renderGrid(releases) {
  const grid = document.getElementById('releases-grid');

  if (releases.length === 0) {
    grid.innerHTML = `
      <p class="placeholder-text">No releases match your filters.</p>
    `;
    return;
  }

  grid.innerHTML = releases.map(r => `
    <div class="card release-card">
      <!-- Artwork — falls back to a type badge if no image URL -->
      <div class="release-artwork">
        ${r.artwork_url
          ? `<img
               src="${escapeHtml(r.artwork_url)}"
               alt="${escapeHtml(r.title)} artwork"
               class="artwork-img"
             />`
          : `<div class="artwork-placeholder">
               <span>${escapeHtml(r.release_type || '♪')}</span>
             </div>`
        }
      </div>

      <div class="release-info">
        <h3 class="release-title">${escapeHtml(r.title)}</h3>
        <p class="card-tag">${escapeHtml(r.release_type)}</p>
        ${r.genre
          ? `<p class="card-meta">${escapeHtml(r.genre)}</p>`
          : ''}
        ${r.release_date
          ? `<p class="card-meta release-date">
               ${formatDate(r.release_date)}
             </p>`
          : ''}
      </div>

      <!-- Action row: View artist + Stream link -->
      <div class="release-actions">
        <a
          href="artist-profile.html?id=${r.artist_id}"
          class="btn btn-secondary release-btn"
        >View Artist</a>
        ${r.streaming_url
          ? `<a
               href="${escapeHtml(r.streaming_url)}"
               target="_blank"
               rel="noopener noreferrer"
               class="btn btn-primary release-btn"
             >Stream ↗</a>`
          : `<span class="btn btn-secondary release-btn" style="opacity:.5;">
               No Stream Link
             </span>`
        }
      </div>
    </div>
  `).join('');
}

/**
 * Render release-type filter chips above the grid.
 */
function renderTypeChips() {
  const container = document.getElementById('type-chips');
  if (!container) return;

  // "All" chip first.
  const chips = [{ label: 'All', value: null }, ...RELEASE_TYPES.map(
    t => ({ label: t, value: t })
  )];

  container.innerHTML = chips.map(chip => `
    <button
      class="chip ${activeTypeFilter === chip.value ? 'chip-active' : ''}"
      data-type="${chip.value ?? ''}"
    >${escapeHtml(chip.label)}</button>
  `).join('');

  container.querySelectorAll('.chip').forEach(btn => {
    btn.addEventListener('click', () => {
      const val = btn.dataset.type || null;
      activeTypeFilter = val;
      renderTypeChips();      // re-render to update active state
      applyFiltersAndRender();
    });
  });
}

/**
 * Render Previous / Next pagination buttons.
 *
 * @param {{ page: number, pages: number }} pagination
 */
function renderPagination(pagination) {
  const container = document.getElementById('pagination');
  const { page, pages } = pagination;

  if (pages <= 1) {
    container.innerHTML = '';
    return;
  }

  container.innerHTML = `
    <button
      class="btn btn-secondary"
      id="btn-prev"
      ${page <= 1 ? 'disabled' : ''}
    >← Previous</button>
    <span class="page-indicator">Page ${page} of ${pages}</span>
    <button
      class="btn btn-secondary"
      id="btn-next"
      ${page >= pages ? 'disabled' : ''}
    >Next →</button>
  `;

  document.getElementById('btn-prev')
    ?.addEventListener('click', () => loadPage(page - 1));
  document.getElementById('btn-next')
    ?.addEventListener('click', () => loadPage(page + 1));
}

// ------------------------------------------------------------------ //
// Search & genre filter                                                //
// ------------------------------------------------------------------ //

/** Debounce the title search input — re-filters locally, no API call. */
function bindSearch() {
  const input = document.getElementById('search-input');
  if (!input) return;
  let timer;
  input.addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(applyFiltersAndRender, 250);
  });
}

/**
 * Genre select change triggers a fresh API call with the genre param.
 * Resets to page 1.
 */
function bindGenreSelect() {
  const sel = document.getElementById('genre-select');
  if (!sel) return;
  sel.addEventListener('change', () => loadPage(1));
}

// ------------------------------------------------------------------ //
// Helpers                                                              //
// ------------------------------------------------------------------ //

/**
 * Format an ISO date string "YYYY-MM-DD" to a human-readable form.
 *
 * @param {string} iso - ISO date string from the API.
 * @returns {string}   - e.g. "Jun 1, 2024"
 */
function formatDate(iso) {
  try {
    // Append T00:00:00 to prevent UTC→local timezone shift on date-only strings.
    return new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, {
      year: 'numeric', month: 'short', day: 'numeric',
    });
  } catch {
    return iso;
  }
}

/**
 * Escape user-supplied strings before inserting into innerHTML.
 *
 * @param {string} str - Raw string.
 * @returns {string}   - HTML-safe string.
 */
function escapeHtml(str) {
  const div = document.createElement('div');
  div.appendChild(document.createTextNode(String(str)));
  return div.innerHTML;
}
