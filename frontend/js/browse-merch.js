/**
 * browse-merch.js
 *
 * Drives the browse-merch.html page.
 *
 * Responsibilities:
 *   1. On load — fetch GET /api/merch and render product cards.
 *   2. Title search — debounced client-side filter on the current page.
 *   3. Stock filter chips — "All" / "In Stock" toggle.
 *   4. Pagination — Previous / Next controls.
 *
 * No purchases are processed in the MVP. The "Buy" button is rendered
 * as a disabled placeholder with a tooltip explaining that purchasing
 * will be available in a future release. This makes the catalog intent
 * clear without misleading users.
 *
 * All HTTP calls go through window.api (api.js).
 * All API-supplied text is escaped before insertion into innerHTML.
 */

const PER_PAGE = 20;

let currentPage   = 1;
let activeStock   = 'all';   // 'all' | 'in-stock'
let allProducts   = [];      // current page, unfiltered

// ------------------------------------------------------------------ //
// Bootstrap                                                            //
// ------------------------------------------------------------------ //

document.addEventListener('DOMContentLoaded', () => {
  loadPage(1);
  bindSearch();
  bindStockChips();
});

// ------------------------------------------------------------------ //
// Data loading                                                         //
// ------------------------------------------------------------------ //

/**
 * Fetch a page of products from the API and render the grid.
 *
 * @param {number} page - 1-based page number.
 */
async function loadPage(page) {
  const grid    = document.getElementById('merch-grid');
  const summary = document.getElementById('results-summary');

  grid.innerHTML = `
    <div class="card placeholder">
      <div class="placeholder-img"></div>
      <p class="placeholder-text">Loading…</p>
    </div>
  `;

  try {
    const data = await api.get(
      `/merch?page=${page}&per_page=${PER_PAGE}`
    );
    allProducts  = data.products || [];
    currentPage  = data.page;

    summary.textContent = data.total === 0
      ? 'No products found.'
      : `${data.total} product${data.total === 1 ? '' : 's'}`;

    applyFiltersAndRender();
    renderPagination(data);
  } catch (err) {
    grid.innerHTML = `
      <div class="alert alert-error">
        Could not load products: ${escapeHtml(err.message)}
      </div>
    `;
    summary.textContent = '';
  }
}

// ------------------------------------------------------------------ //
// Rendering                                                            //
// ------------------------------------------------------------------ //

/** Apply active filters then re-render the grid. */
function applyFiltersAndRender() {
  const search = (
    document.getElementById('search-input')?.value || ''
  ).trim().toLowerCase();

  let filtered = allProducts;

  // Stock chip filter — null inventory = unlimited (in stock).
  if (activeStock === 'in-stock') {
    filtered = filtered.filter(
      p => p.inventory_quantity === null || p.inventory_quantity > 0
    );
  }

  // Title search filter.
  if (search) {
    filtered = filtered.filter(
      p => p.product_name.toLowerCase().includes(search)
    );
  }

  renderGrid(filtered);
}

/**
 * Render product cards into the grid.
 *
 * @param {Array} products - MerchProduct objects from the API.
 */
function renderGrid(products) {
  const grid = document.getElementById('merch-grid');

  if (products.length === 0) {
    grid.innerHTML = `
      <p class="placeholder-text">No products match your filters.</p>
    `;
    return;
  }

  grid.innerHTML = products.map(p => {
    const inStock = p.inventory_quantity === null || p.inventory_quantity > 0;
    const stockLabel = p.inventory_quantity === null
      ? ''
      : p.inventory_quantity === 0
        ? '<span class="stock-badge stock-out">Out of stock</span>'
        : `<span class="stock-badge stock-in">${p.inventory_quantity} left</span>`;

    return `
      <div class="card merch-card ${inStock ? '' : 'merch-card-oos'}">
        <!-- Product image -->
        <div class="merch-artwork">
          ${p.image_url
            ? `<img
                 src="${escapeHtml(p.image_url)}"
                 alt="${escapeHtml(p.product_name)}"
                 class="artwork-img"
               />`
            : `<div class="artwork-placeholder">
                 <span class="merch-icon">🛍️</span>
               </div>`
          }
        </div>

        <div class="merch-info">
          <h3 class="merch-name">${escapeHtml(p.product_name)}</h3>
          ${p.description
            ? `<p class="merch-desc">${escapeHtml(
                p.description.length > 80
                  ? p.description.slice(0, 80) + '…'
                  : p.description
              )}</p>`
            : ''
          }
          ${stockLabel}
        </div>

        <div class="merch-footer">
          <span class="merch-price">$${p.price.toFixed(2)}</span>
          <div class="merch-actions">
            <a
              href="artist-profile.html?id=${p.artist_id}"
              class="btn btn-secondary merch-btn"
            >Artist</a>
            <!--
              Purchasing is not yet implemented (MVP catalog only).
              The button is rendered disabled with a tooltip so the
              intent is clear. Phase 3 will wire this to POST /api/orders.
            -->
            <button
              class="btn btn-primary merch-btn"
              disabled
              title="Purchasing coming soon — catalog only in MVP"
              aria-label="Add to cart — coming soon"
            >Buy</button>
          </div>
        </div>
      </div>
    `;
  }).join('');
}

/**
 * Render Previous / Next pagination buttons.
 *
 * @param {{ page: number, pages: number }} data
 */
function renderPagination(data) {
  const container = document.getElementById('pagination');
  const { page, pages } = data;

  if (pages <= 1) {
    container.innerHTML = '';
    return;
  }

  container.innerHTML = `
    <button class="btn btn-secondary" id="btn-prev"
      ${page <= 1 ? 'disabled' : ''}>← Previous</button>
    <span class="page-indicator">Page ${page} of ${pages}</span>
    <button class="btn btn-secondary" id="btn-next"
      ${page >= pages ? 'disabled' : ''}>Next →</button>
  `;

  document.getElementById('btn-prev')
    ?.addEventListener('click', () => loadPage(page - 1));
  document.getElementById('btn-next')
    ?.addEventListener('click', () => loadPage(page + 1));
}

// ------------------------------------------------------------------ //
// Filters                                                              //
// ------------------------------------------------------------------ //

/** Debounce title search — re-filters locally, no API call. */
function bindSearch() {
  const input = document.getElementById('search-input');
  if (!input) return;
  let timer;
  input.addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(applyFiltersAndRender, 250);
  });
}

/** Wire stock filter chips. */
function bindStockChips() {
  const container = document.getElementById('stock-chips');
  if (!container) return;

  container.addEventListener('click', e => {
    const btn = e.target.closest('.chip');
    if (!btn) return;

    activeStock = btn.dataset.stock;

    // Update active chip appearance.
    container.querySelectorAll('.chip').forEach(c => {
      c.classList.toggle('chip-active', c === btn);
    });

    applyFiltersAndRender();
  });
}

// ------------------------------------------------------------------ //
// Helpers                                                              //
// ------------------------------------------------------------------ //

/**
 * Escape user-supplied strings before inserting into innerHTML.
 *
 * @param {string} str
 * @returns {string}
 */
function escapeHtml(str) {
  const div = document.createElement('div');
  div.appendChild(document.createTextNode(String(str)));
  return div.innerHTML;
}
