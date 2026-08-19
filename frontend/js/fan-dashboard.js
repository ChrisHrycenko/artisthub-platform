/**
 * fan-dashboard.js
 *
 * Drives the fan-dashboard.html page.
 *
 * Flow:
 *   1. Call GET /api/auth/me — redirect to login if not authenticated
 *      as a fan.
 *   2. Fetch the list of followed artists (GET /api/follows).
 *   3. Render followed artist cards.
 *   4. Fetch and render a combined post feed from all followed artists.
 *
 * All HTTP calls go through window.api (api.js).
 */

document.addEventListener('DOMContentLoaded', async () => {
  const loading = document.getElementById('dashboard-loading');
  const errDiv  = document.getElementById('dashboard-error');
  const content = document.getElementById('dashboard-content');

  // ---------------------------------------------------------------- //
  // 1. Auth check — must be a fan session                             //
  // ---------------------------------------------------------------- //
  let me;
  try {
    me = await api.get('/auth/me');
  } catch {
    me = null;
  }

  if (!me || me.role !== 'fan') {
    loading.style.display = 'none';
    errDiv.style.display  = '';
    return;
  }

  // ---------------------------------------------------------------- //
  // 2. Welcome message                                                //
  // ---------------------------------------------------------------- //
  document.getElementById('dash-welcome').textContent =
    `Welcome back, ${me.username}!`;
  document.getElementById('dash-email').textContent = me.email;

  // ---------------------------------------------------------------- //
  // 3. Fetch followed artists                                         //
  // ---------------------------------------------------------------- //
  let followedArtists = [];
  try {
    const data    = await api.get('/follows');
    followedArtists = data.following || [];
  } catch {
    // Non-critical — show empty state.
  }

  document.getElementById('stat-following').textContent =
    followedArtists.length;

  // ---------------------------------------------------------------- //
  // 4. Render followed artist cards                                   //
  // ---------------------------------------------------------------- //
  const followingGrid  = document.getElementById('following-grid');
  const followingEmpty = document.getElementById('following-empty');

  if (followedArtists.length === 0) {
    followingEmpty.style.display = '';
  } else {
    followingGrid.innerHTML = followedArtists.map(a => `
      <a href="artist-profile.html?id=${a.id}" class="card artist-card">
        <div class="artist-avatar avatar avatar-initials">
          ${escapeHtml(initials(a.display_name))}
        </div>
        <div class="artist-info">
          <h3 class="artist-name">${escapeHtml(a.display_name)}</h3>
          ${a.genre
            ? `<p class="card-meta">${escapeHtml(a.genre)}</p>`
            : ''}
          ${a.location
            ? `<p class="card-meta">${escapeHtml(a.location)}</p>`
            : ''}
          <p class="card-tag">
            ${a.follower_count} follower${a.follower_count === 1 ? '' : 's'}
          </p>
        </div>
      </a>
    `).join('');
  }

  // ---------------------------------------------------------------- //
  // 5. Build post feed from followed artists                          //
  // ---------------------------------------------------------------- //
  await loadFeed(followedArtists);

  // Reveal the dashboard.
  loading.style.display = 'none';
  content.style.display = '';
});

// ------------------------------------------------------------------ //
// Feed                                                                 //
// ------------------------------------------------------------------ //

/**
 * Fetch recent posts from each followed artist in parallel and render
 * a unified, time-sorted feed.
 *
 * Silently skips artists whose post fetch fails (network resilience).
 *
 * @param {Array} artists - List of followed artist objects.
 */
async function loadFeed(artists) {
  const feedList  = document.getElementById('feed-list');
  const feedEmpty = document.getElementById('feed-empty');

  if (artists.length === 0) {
    feedEmpty.style.display = '';
    return;
  }

  // Fetch up to 5 posts per followed artist in parallel.
  const results = await Promise.allSettled(
    artists.map(a =>
      api.get(`/artists/${a.id}/posts?per_page=5`).then(d => ({
        artist: a,
        posts: d.posts || [],
      }))
    )
  );

  // Flatten all posts into a single array with artist metadata attached.
  const allPosts = results
    .filter(r => r.status === 'fulfilled')
    .flatMap(r => r.value.posts.map(p => ({
      ...p,
      _artist: r.value.artist,
    })));

  // Sort by created_at descending (newest first).
  allPosts.sort(
    (a, b) => new Date(b.created_at) - new Date(a.created_at)
  );

  if (allPosts.length === 0) {
    feedEmpty.style.display = '';
    return;
  }

  feedList.innerHTML = allPosts.map(p => `
    <article class="post-card">
      <div class="post-avatar avatar avatar-initials">
        ${escapeHtml(initials(p._artist.display_name))}
      </div>
      <div class="post-body">
        <div class="post-header">
          <a class="post-artist-name" href="artist-profile.html?id=${
            p._artist.id
          }">${escapeHtml(p._artist.display_name)}</a>
          <time class="post-time" datetime="${escapeHtml(p.created_at)}">
            ${formatRelativeTime(p.created_at)}
          </time>
        </div>
        <p class="post-text">${escapeHtml(p.body)}</p>
        ${p.image_url
          ? `<div class="post-image-wrap">
               <img src="${escapeHtml(p.image_url)}" alt="Post image"
                    class="post-image" loading="lazy" />
             </div>`
          : ''}
      </div>
    </article>
  `).join('');
}

// ------------------------------------------------------------------ //
// Helpers                                                              //
// ------------------------------------------------------------------ //

/** Derive up to two initials from a display name. */
function initials(name) {
  return (name || '?')
    .split(' ')
    .slice(0, 2)
    .map(w => w[0] || '')
    .join('')
    .toUpperCase();
}

/** Format an ISO datetime string as a relative time label. */
function formatRelativeTime(iso) {
  try {
    const diff  = Date.now() - new Date(iso).getTime();
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

/** Escape user-supplied strings before inserting into innerHTML. */
function escapeHtml(str) {
  const div = document.createElement('div');
  div.appendChild(document.createTextNode(String(str)));
  return div.innerHTML;
}
