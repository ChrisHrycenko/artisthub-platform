/**
 * browse-posts.js
 *
 * Drives the browse-posts.html global feed page.
 *
 * Responsibilities:
 *   1. On load — fetch GET /api/posts and render post cards.
 *   2. Each card links back to the artist profile page.
 *   3. Pagination — Previous / Next controls.
 *
 * All HTTP calls go through window.api (api.js).
 * All API-supplied text is escaped before insertion into innerHTML.
 */

const PER_PAGE = 20;

document.addEventListener('DOMContentLoaded', () => {
  loadPage(1);
});

// ------------------------------------------------------------------ //
// Data loading                                                         //
// ------------------------------------------------------------------ //

/**
 * Fetch a page of posts from the global feed and render them.
 *
 * @param {number} page - 1-based page number.
 */
async function loadPage(page) {
  const feed    = document.getElementById('posts-feed');
  const summary = document.getElementById('results-summary');

  feed.innerHTML = `
    <div class="post-card skeleton-card">
      <div class="placeholder-img"
           style="height:48px;border-radius:50%;width:48px;flex-shrink:0;">
      </div>
      <div style="flex:1;">
        <div class="placeholder-text"
             style="height:.9rem;width:35%;margin-bottom:.4rem;"></div>
        <div class="placeholder-text" style="height:.8rem;width:70%;"></div>
      </div>
    </div>
  `;

  try {
    const data = await api.get(
      `/posts?page=${page}&per_page=${PER_PAGE}`
    );
    const posts = data.posts || [];

    summary.textContent = data.total === 0
      ? 'No posts yet.'
      : `${data.total} post${data.total === 1 ? '' : 's'}`;

    renderFeed(feed, posts);
    renderPagination(data);
  } catch (err) {
    feed.innerHTML = `
      <div class="alert alert-error">
        Could not load posts: ${escapeHtml(err.message)}
      </div>
    `;
    summary.textContent = '';
  }
}

// ------------------------------------------------------------------ //
// Rendering                                                            //
// ------------------------------------------------------------------ //

/**
 * Render an array of post objects as cards in the feed container.
 *
 * @param {HTMLElement} container - The feed element.
 * @param {Array}       posts     - Post objects from the API.
 */
function renderFeed(container, posts) {
  if (posts.length === 0) {
    container.innerHTML = `
      <p class="placeholder-text">
        No posts yet — check back soon!
      </p>
    `;
    return;
  }

  container.innerHTML = posts.map(p => `
    <article class="post-card">
      <!-- Artist avatar link -->
      <a
        href="artist-profile.html?id=${p.artist_id}"
        class="post-avatar"
        aria-label="View artist profile"
      >
        <span class="avatar-initials avatar-sm">♪</span>
      </a>

      <div class="post-body">
        <!-- Header: artist link + timestamp -->
        <div class="post-header">
          <a
            href="artist-profile.html?id=${p.artist_id}"
            class="post-artist-link"
          >Artist #${p.artist_id}</a>
          <time
            class="post-time"
            datetime="${escapeHtml(p.created_at)}"
            title="${escapeHtml(p.created_at)}"
          >${formatRelativeTime(p.created_at)}</time>
        </div>

        <!-- Post text -->
        <p class="post-text">${escapeHtml(p.body)}</p>

        <!-- Optional image -->
        ${p.image_url
          ? `<div class="post-image-wrap">
               <img
                 src="${escapeHtml(p.image_url)}"
                 alt="Post image"
                 class="post-image"
                 loading="lazy"
               />
             </div>`
          : ''
        }
      </div>
    </article>
  `).join('');
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
// Helpers                                                              //
// ------------------------------------------------------------------ //

/**
 * Format an ISO datetime string as a relative time label.
 * Falls back to a short date if the timestamp is older than 7 days.
 *
 * @param {string} iso - ISO 8601 datetime string from the API.
 * @returns {string}   - e.g. "2 hours ago", "3 days ago", "Jun 1, 2024"
 */
function formatRelativeTime(iso) {
  try {
    const diff = Date.now() - new Date(iso).getTime();
    const mins  = Math.floor(diff / 60_000);
    const hours = Math.floor(diff / 3_600_000);
    const days  = Math.floor(diff / 86_400_000);

    if (mins < 1)   return 'just now';
    if (mins < 60)  return `${mins}m ago`;
    if (hours < 24) return `${hours}h ago`;
    if (days < 7)   return `${days}d ago`;

    return new Date(iso).toLocaleDateString(undefined, {
      year: 'numeric', month: 'short', day: 'numeric',
    });
  } catch {
    return iso;
  }
}

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
