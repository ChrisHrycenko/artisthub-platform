/**
 * artist-analytics.js
 *
 * Drives the artist-analytics.html page.
 *
 * Flow
 * ----
 * 1. Read ?id=<artist_id> from the URL query string.
 * 2. Call GET /api/artists/<id>/analytics — one request that returns all
 *    four core metrics in a single response.
 * 3. Render the stat cards (follower_count, release_count, post_count,
 *    merch_count).
 * 4. Render an inline SVG bar chart showing the catalogue breakdown.
 * 5. Wire the Refresh button to re-fetch and re-render without a page reload.
 * 6. If ?id is absent or the API returns 404, show the error panel.
 *
 * No third-party libraries are used.  The chart is plain inline SVG so the
 * page loads with zero extra network requests.
 *
 * All HTTP calls go through window.api (api.js).
 * All API-supplied text is escaped before insertion into innerHTML.
 *
 * --- How to extend this page as analytics expand ---
 *
 * Streaming analytics
 * -------------------
 * When GET /api/artists/<id>/analytics gains `total_streams`,
 * `streams_last_30_days`, and `streams_by_release` keys, add:
 *
 *   1. Two new stat cards in renderStatCards() for total/monthly streams.
 *   2. A second SVG chart (or a Chart.js LineChart if you want time-series)
 *      in a new section beneath the bar chart:
 *
 *        const canvas = document.getElementById('streams-chart');
 *        new Chart(canvas, {
 *          type: 'line',
 *          data: {
 *            labels: data.streams_by_date.map(d => d.date),
 *            datasets: [{ label: 'Streams', data:
 *              data.streams_by_date.map(d => d.count) }]
 *          }
 *        });
 *
 *   IBM Confluent note: once stream events flow through Kafka into the DB,
 *   the API can add a `?since=` query param so this page can show a rolling
 *   7-day or 30-day window without changing the URL structure:
 *     api.get(`/artists/${id}/analytics?since=30d`)
 *
 * Sales analytics
 * ---------------
 * When `total_revenue` and `orders_by_product` appear in the payload:
 *   1. Add a revenue stat card: formatCurrency(data.total_revenue)
 *   2. Add a horizontal bar chart showing revenue per merch item, using the
 *      same buildBarChart() helper below — just pass `orders_by_product`
 *      as the series.
 *
 * Audience / follower analytics
 * ------------------------------
 * When `new_followers_last_30_days` and `churn_rate` are available:
 *   1. Add two new stat cards alongside the existing follower_count.
 *   2. For follower_locations: build a simple sorted list of
 *      { location, count } pairs and render them as a mini table.
 *
 * IBM watsonx AI summary
 * ----------------------
 * When the endpoint adds an `ai_summary` string key:
 *   const summaryEl = document.getElementById('ai-summary');
 *   summaryEl.textContent = data.analytics.ai_summary;
 *   summaryEl.closest('.ai-card').style.display = '';
 * No other JS changes required — the card is already in the HTML, hidden.
 */

document.addEventListener('DOMContentLoaded', async () => {
  // ---------------------------------------------------------------- //
  // 1. Read ?id from the URL                                          //
  // ---------------------------------------------------------------- //
  const params   = new URLSearchParams(window.location.search);
  const artistId = params.get('id');

  if (!artistId) {
    showError('No artist ID provided. Please go back and select an artist.');
    return;
  }

  // ---------------------------------------------------------------- //
  // 2. Fetch analytics                                                //
  // ---------------------------------------------------------------- //
  await loadAnalytics(artistId);

  // ---------------------------------------------------------------- //
  // 3. Wire Refresh button                                            //
  // ---------------------------------------------------------------- //
  document.getElementById('analytics-refresh-btn')
    .addEventListener('click', () => loadAnalytics(artistId));
});


// ------------------------------------------------------------------ //
// Core loader                                                          //
// ------------------------------------------------------------------ //

/**
 * Fetch analytics for `artistId` and (re-)render the entire page.
 *
 * Called once on load and again whenever the user clicks Refresh.
 * On success: populates all sections and shows #analytics-content.
 * On failure: shows #analytics-error with the API's message.
 *
 * @param {string|number} artistId - The artist's primary key.
 */
async function loadAnalytics(artistId) {
  // Show loading skeleton while the request is in flight.
  setVisibility('analytics-loading', true);
  setVisibility('analytics-content', false);
  setVisibility('analytics-error', false);

  let data;
  try {
    const res = await api.get(`/artists/${artistId}/analytics`);
    data = res.analytics;
  } catch (err) {
    showError(err.message || 'Could not load analytics. Please try again.');
    return;
  }

  // ---------------------------------------------------------------- //
  // Populate header band                                              //
  // ---------------------------------------------------------------- //
  document.getElementById('analytics-artist-name').textContent =
    data.display_name;

  document.getElementById('analytics-profile-link').href =
    `artist-profile.html?id=${data.artist_id}`;

  document.title = `${data.display_name} Analytics — ArtistHub`;

  // Show data freshness: "Updated just now" or "Updated 3m ago".
  document.getElementById('analytics-freshness').textContent =
    `Updated ${formatRelativeTime(data.generated_at)}`;

  // ---------------------------------------------------------------- //
  // Render stat cards                                                 //
  // ---------------------------------------------------------------- //
  renderStatCards(data);

  // ---------------------------------------------------------------- //
  // Render bar chart                                                  //
  // ---------------------------------------------------------------- //
  buildBarChart(data);

  // Reveal content, hide skeleton.
  setVisibility('analytics-loading', false);
  setVisibility('analytics-content', true);
}


// ------------------------------------------------------------------ //
// Stat cards                                                           //
// ------------------------------------------------------------------ //

/**
 * Build and inject the four core metric cards into #core-stats-grid.
 *
 * Each card is a .stat-card div with a large number and a label.
 * The colours are tied to CSS custom properties defined in main.css.
 *
 * When new metrics are added to the API response (e.g. `total_streams`),
 * append a new entry to the `cards` array — nothing else needs to change.
 *
 * @param {object} data - The analytics object from the API response.
 */
function renderStatCards(data) {
  const grid = document.getElementById('core-stats-grid');

  /**
   * Each entry: { value, label, colorClass }
   *
   * colorClass maps to a CSS modifier on .stat-card:
   *   stat-card--followers  → purple accent (audience)
   *   stat-card--releases   → blue accent   (catalogue)
   *   stat-card--posts      → teal accent   (engagement)
   *   stat-card--merch      → amber accent  (revenue)
   *
   * Future metrics to add here:
   *   { value: data.total_streams,          label: 'Total Streams',
   *     colorClass: 'stat-card--streams' }
   *   { value: formatCurrency(data.total_revenue), label: 'Total Revenue',
   *     colorClass: 'stat-card--revenue' }
   *   { value: data.new_followers_last_30_days,
   *     label: 'New Followers (30d)',       colorClass: 'stat-card--new-fans' }
   */
  const cards = [
    {
      value:      data.follower_count,
      label:      'Followers',
      colorClass: 'stat-card--followers',
      // Future: add a sub-label "+N this month" once time-series data exists.
    },
    {
      value:      data.release_count,
      label:      'Releases',
      colorClass: 'stat-card--releases',
    },
    {
      value:      data.post_count,
      label:      'Posts',
      colorClass: 'stat-card--posts',
    },
    {
      value:      data.merch_count,
      label:      'Merch Items',
      colorClass: 'stat-card--merch',
    },
  ];

  grid.innerHTML = cards.map(c => `
    <div class="stat-card ${escapeHtml(c.colorClass)}">
      <span class="stat-number">${escapeHtml(String(c.value))}</span>
      <span class="stat-label">${escapeHtml(c.label)}</span>
    </div>
  `).join('');
}


// ------------------------------------------------------------------ //
// Bar chart                                                            //
// ------------------------------------------------------------------ //

/**
 * Render an inline SVG horizontal bar chart of the catalogue counts.
 *
 * Why inline SVG?
 *   - Zero dependencies, zero extra network requests.
 *   - Fully accessible via role + aria-label on the <svg> element.
 *   - Easy to extend: add a new bar by appending an entry to `series`.
 *
 * When to switch to Chart.js / D3:
 *   Once time-series data (streams per day, revenue per week) is
 *   available, an SVG bar chart is not the right tool.  At that point,
 *   replace this section with a <canvas id="streams-chart"> and wire
 *   it to Chart.js LineChart.  The rest of the page (stat cards, header,
 *   roadmap section) requires no changes.
 *
 * @param {object} data - The analytics object from the API response.
 */
function buildBarChart(data) {
  const svg = document.getElementById('catalogue-chart');

  /**
   * Series definition — each entry becomes one horizontal bar.
   *
   * Extend this array when new catalogue-type metrics are added, e.g.:
   *   { label: 'Streams', value: data.total_streams, color: '#0ea5e9' }
   */
  const series = [
    { label: 'Releases',   value: data.release_count, color: '#3b82d4' },
    { label: 'Posts',      value: data.post_count,    color: '#7c5cd8' },
    { label: 'Merch Items', value: data.merch_count,  color: '#f59e0b' },
    { label: 'Followers',  value: data.follower_count, color: '#10b981' },
  ];

  const maxValue = Math.max(...series.map(s => s.value), 1);

  // Chart geometry constants (all in SVG user units = px at 1× zoom).
  const W          = 560;   // total SVG width
  const barHeight  = 32;    // height of each filled bar rect
  const rowH       = 52;    // total height per row (bar + padding)
  const labelW     = 96;    // reserved width for the row label on the left
  const valueW     = 40;    // reserved width for the number on the right
  const barAreaW   = W - labelW - valueW - 16; // drawable bar area
  const H          = rowH * series.length + 16; // total SVG height

  // Update the SVG element's dimensions so it scales correctly.
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.setAttribute('width',  '100%');
  svg.setAttribute('height', H);

  svg.innerHTML = series.map((s, i) => {
    const barW = maxValue > 0
      ? Math.round((s.value / maxValue) * barAreaW)
      : 0;
    const y = i * rowH + 8;  // top edge of this row

    // Accessible: each bar group has an aria-label with full details.
    return `
      <g role="graphics-symbol"
         aria-label="${escapeHtml(s.label)}: ${s.value}">

        <!-- Row label (left-aligned) -->
        <text
          x="${labelW - 8}"
          y="${y + barHeight / 2 + 5}"
          text-anchor="end"
          class="chart-label"
          fill="#57606a"
          font-size="13"
          font-family="-apple-system, Segoe UI, system-ui, sans-serif"
        >${escapeHtml(s.label)}</text>

        <!-- Background track -->
        <rect
          x="${labelW}"
          y="${y}"
          width="${barAreaW}"
          height="${barHeight}"
          rx="4"
          fill="#f3f4f6"
        />

        <!-- Filled bar — width is proportional to max value -->
        ${barW > 0 ? `
        <rect
          x="${labelW}"
          y="${y}"
          width="${barW}"
          height="${barHeight}"
          rx="4"
          fill="${s.color}"
          opacity="0.85"
        />` : ''}

        <!-- Value label (right of bar area) -->
        <text
          x="${labelW + barAreaW + 8}"
          y="${y + barHeight / 2 + 5}"
          text-anchor="start"
          fill="#1f2328"
          font-size="13"
          font-weight="700"
          font-family="-apple-system, Segoe UI, system-ui, sans-serif"
        >${s.value}</text>

      </g>
    `;
  }).join('');
}


// ------------------------------------------------------------------ //
// Helpers                                                              //
// ------------------------------------------------------------------ //

/**
 * Show one of the three page panels, hide the other two.
 * Uses display-style toggling rather than CSS classes so the panels are
 * never visible simultaneously.
 *
 * @param {string}  id      - Element ID to show or hide.
 * @param {boolean} visible - true = show, false = hide.
 */
function setVisibility(id, visible) {
  const el = document.getElementById(id);
  if (el) el.style.display = visible ? '' : 'none';
}

/**
 * Reveal the error panel with a message.
 * Hides the loading skeleton and main content.
 *
 * @param {string} message - Human-readable error text.
 */
function showError(message) {
  setVisibility('analytics-loading', false);
  setVisibility('analytics-content', false);
  setVisibility('analytics-error', true);
  const msgEl = document.getElementById('analytics-error-message');
  if (msgEl) msgEl.textContent = message;
}

/**
 * Format an ISO-8601 datetime string as a relative time label.
 * Used to show how fresh the analytics snapshot is.
 *
 * @param {string} iso - ISO-8601 datetime ending in "Z" (UTC).
 * @returns {string}   - e.g. "just now", "3m ago", "2h ago".
 */
function formatRelativeTime(iso) {
  try {
    const diff  = Date.now() - new Date(iso).getTime();
    const secs  = Math.floor(diff / 1_000);
    const mins  = Math.floor(diff / 60_000);
    const hours = Math.floor(diff / 3_600_000);
    if (secs < 5)    return 'just now';
    if (secs < 60)   return `${secs}s ago`;
    if (mins < 60)   return `${mins}m ago`;
    if (hours < 24)  return `${hours}h ago`;
    return new Date(iso).toLocaleDateString(undefined, {
      year: 'numeric', month: 'short', day: 'numeric',
    });
  } catch {
    return iso;
  }
}

/**
 * Escape user-supplied strings before inserting into innerHTML.
 * Prevents XSS — always use this when rendering API data as HTML.
 *
 * @param {string} str - Raw string.
 * @returns {string}   - HTML-safe string.
 */
function escapeHtml(str) {
  const div = document.createElement('div');
  div.appendChild(document.createTextNode(String(str)));
  return div.innerHTML;
}
