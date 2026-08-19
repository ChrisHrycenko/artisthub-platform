/**
 * browse.js
 *
 * Artist discovery logic for index.html.
 *
 * On page load, fetches GET /api/artists and renders artist cards
 * into #artists-grid. This is a stub for Phase 1 — the /api/artists
 * endpoint is implemented in Phase 2. For now it shows a "coming soon"
 * message so the page renders cleanly without a real API response.
 */

document.addEventListener('DOMContentLoaded', async () => {
  const grid = document.getElementById('artists-grid');
  if (!grid) return;

  try {
    // GET /api/artists — implemented in Phase 2.
    const data = await api.get('/artists');
    renderArtists(grid, data.artists || []);
  } catch {
    // Phase 1: endpoint not yet implemented — show placeholder.
    grid.innerHTML = `
      <div class="card">
        <h3>Artists coming soon</h3>
        <p>Artist profiles will appear here once Phase 2 is complete.</p>
      </div>
    `;
  }
});

/**
 * Render an array of artist objects as cards inside a grid container.
 *
 * @param {HTMLElement} container - The grid element to render into.
 * @param {Array}       artists   - Array of artist objects from the API.
 */
function renderArtists(container, artists) {
  if (artists.length === 0) {
    container.innerHTML = '<p class="placeholder-text">No artists yet. Be the first to join!</p>';
    return;
  }

  container.innerHTML = artists.map(artist => `
    <a href="artist-profile.html?id=${artist.id}" class="card">
      <h3>${escapeHtml(artist.display_name)}</h3>
      <p>${escapeHtml(artist.genre || 'Independent')}</p>
      ${artist.location ? `<p>${escapeHtml(artist.location)}</p>` : ''}
    </a>
  `).join('');
}

/**
 * Escape user-supplied strings before inserting into innerHTML.
 * Prevents XSS — always use this when rendering API data into HTML.
 *
 * @param {string} str - Raw string from API response.
 * @returns {string}   - HTML-safe string.
 */
function escapeHtml(str) {
  const div = document.createElement('div');
  div.appendChild(document.createTextNode(str));
  return div.innerHTML;
}
