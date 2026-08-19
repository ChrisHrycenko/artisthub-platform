/**
 * browse-artists.js
 *
 * Drives the browse-artists.html page.
 *
 * Responsibilities:
 *   1. On load — fetch GET /api/artists and render artist cards.
 *   2. Search input — debounce keystrokes and re-render matching cards
 *      client-side (no extra API call needed at MVP scale).
 *   3. Pagination — render Previous / Next controls and load pages.
 *
 * All HTTP calls go through window.api (api.js). escapeHtml() is used
 * on every piece of API-supplied text before injecting into innerHTML.
 */

// ------------------------------------------------------------------ //
// State                                                                //
// ------------------------------------------------------------------ //

/** Current page number (1-based). */
let currentPage = 1;

/** Number of items per request — matches API default. */
const PER_PAGE = 20;

/** All artists on the current page (unfiltered). */
let allArtists = [];

// ------------------------------------------------------------------ //
// Bootstrap                                                            //
// ------------------------------------------------------------------ //

document.addEventListener('DOMContentLoaded', () => {
  loadPage(1);
  bindSearch();
});

// ------------------------------------------------------------------ //
// Data loading                                                         //
// ------------------------------------------------------------------ //

/**
 * Fetch a page of artists from the API and render the grid.
 *
 * @param {number} page - 1-based page number to load.
 */
async function loadPage(page) {
  const grid    = document.getElementById('artists-grid');
  const summary = document.getElementById('results-summary');

  // Show loading skeleton while the request is in flight.
  grid.innerHTML = `
    <div class="card placeholder">
      <div class="placeholder-img"></div>
      <p class="placeholder-text">Loading…</p>
    </div>
  `;

  try {
    const data = await api.get(
      `/artists?page=${page}&per_page=${PER_PAGE}`
    );

    allArtists = data.artists || [];
    currentPage = data.page;

    // Update summary line.
    summary.textContent = data.total === 0
      ? 'No artists found.'
      : `Showing ${allArtists.length} of ${data.total} artist${data.total === 1 ? '' : 's'}`;

    renderGrid(allArtists);
    renderPagination(data);

  } catch (err) {
    grid.innerHTML = `
      <div class="alert alert-error">
        Could not load artists: ${escapeHtml(err.message)}
      </div>
    `;
    summary.textContent = '';
  }
}

// ------------------------------------------------------------------ //
// Rendering                                                            //
// ------------------------------------------------------------------ //

/**
 * Render an array of artist objects as clickable cards inside the grid.
 *
 * @param {Array} artists - Artist objects from the API.
 */
function renderGrid(artists) {
  const grid = document.getElementById('artists-grid');

  if (artists.length === 0) {
    grid.innerHTML = `
      <p class="placeholder-text">
        No artists match your search.
      </p>
    `;
    return;
  }

  grid.innerHTML = artists.map(a => `
    <a href="artist-profile.html?id=${a.id}" class="card artist-card">
      <div class="artist-card-avatar" aria-hidden="true">
        ${a.profile_image_url
          ? `<img src="${escapeHtml(a.profile_image_url)}"
                  alt="${escapeHtml(a.display_name)} avatar"
                  class="avatar-img" />`
          : `<span class="avatar-initials">
               ${escapeHtml(initials(a.display_name))}
             </span>`
        }
      </div>
      <h3>${escapeHtml(a.display_name)}</h3>
      ${a.genre
        ? `<p class="card-tag">${escapeHtml(a.genre)}</p>`
        : ''}
      ${a.location
        ? `<p class="card-meta">${escapeHtml(a.location)}</p>`
        : ''}
    </a>
  `).join('');
}

/**
 * Render Previous / Next pagination buttons.
 *
 * @param {{ page: number, pages: number }} pagination - API pagination data.
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
// Search (client-side filter on the current page)                     //
// ------------------------------------------------------------------ //

/**
 * Wire the search input to filter the rendered artist cards.
 *
 * Filters by display_name and genre — both case-insensitive substring
 * match. Debounced at 250ms to avoid filtering on every keystroke.
 */
function bindSearch() {
  const input = document.getElementById('search-input');
  if (!input) return;

  let debounceTimer;
  input.addEventListener('input', () => {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
      const query = input.value.trim().toLowerCase();
      if (!query) {
        renderGrid(allArtists);
        return;
      }
      const filtered = allArtists.filter(a =>
        a.display_name.toLowerCase().includes(query) ||
        (a.genre || '').toLowerCase().includes(query)
      );
      renderGrid(filtered);
    }, 250);
  });
}

// ------------------------------------------------------------------ //
// Helpers                                                              //
// ------------------------------------------------------------------ //

/**
 * Derive up to two initials from a display name, e.g. "Jane Doe" → "JD".
 *
 * @param {string} name - The artist's display name.
 * @returns {string}    - 1–2 uppercase initial characters.
 */
function initials(name) {
  return (name || '?')
    .split(' ')
    .slice(0, 2)
    .map(w => w[0] || '')
    .join('')
    .toUpperCase();
}

/**
 * Escape user-supplied strings before inserting into innerHTML.
 * Prevents XSS — always use this when rendering API data as HTML.
 *
 * @param {string} str - Raw string from API response.
 * @returns {string}   - HTML-safe string.
 */
function escapeHtml(str) {
  const div = document.createElement('div');
  div.appendChild(document.createTextNode(String(str)));
  return div.innerHTML;
}
