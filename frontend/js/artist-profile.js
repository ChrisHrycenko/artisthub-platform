/**
 * artist-profile.js
 *
 * Drives the artist-profile.html page.
 *
 * Flow:
 *   1. Read the `?id=<artist_id>` query parameter from the URL.
 *   2. Call GET /api/artists/<id> to fetch the artist's profile.
 *   3. Populate all elements on the page with the returned data.
 *   4. If the current session user IS the artist, show the Edit button.
 *   5. If the current session user is a Fan, show the Follow/Unfollow button.
 *   6. Handle 404 and network errors gracefully.
 *
 * All HTTP calls go through window.api (api.js).
 * All API-supplied text is escaped before insertion into innerHTML.
 */

document.addEventListener('DOMContentLoaded', async () => {
  // ---------------------------------------------------------------- //
  // 1. Read artist ID from URL query string (?id=42)                  //
  // ---------------------------------------------------------------- //
  const params   = new URLSearchParams(window.location.search);
  const artistId = params.get('id');

  const loading = document.getElementById('profile-loading');
  const content = document.getElementById('profile-content');
  const errDiv  = document.getElementById('profile-error');
  const errMsg  = document.getElementById('profile-error-message');

  if (!artistId) {
    showError('No artist ID provided. Please go back and select an artist.');
    return;
  }

  // ---------------------------------------------------------------- //
  // 2. Fetch artist profile from the API                              //
  // ---------------------------------------------------------------- //
  let artist;
  try {
    const data = await api.get(`/artists/${artistId}`);
    artist = data.artist;
  } catch (err) {
    showError(err.message || 'Artist not found.');
    return;
  }

  // ---------------------------------------------------------------- //
  // 3. Populate page elements                                         //
  // ---------------------------------------------------------------- //

  // Update browser tab title.
  document.title = `${artist.display_name} — ArtistHub`;

  // Artist name.
  document.getElementById('profile-name').textContent = artist.display_name;

  // Subtitle: "Genre · Location" — show only populated fields.
  const subtitleParts = [artist.genre, artist.location].filter(Boolean);
  document.getElementById('profile-genre-location').textContent =
    subtitleParts.join(' · ');

  // Avatar — image if URL provided, otherwise coloured initials circle.
  const avatarEl = document.getElementById('profile-avatar');
  if (artist.profile_image_url) {
    avatarEl.innerHTML = `
      <img
        src="${escapeHtml(artist.profile_image_url)}"
        alt="${escapeHtml(artist.display_name)} profile photo"
        class="avatar-img avatar-lg"
      />
    `;
  } else {
    avatarEl.classList.add('avatar-initials', 'avatar-lg');
    avatarEl.textContent = initials(artist.display_name);
  }

  // Bio — hide the whole section if empty.
  const bioSection = document.getElementById('bio-section');
  const bioEl      = document.getElementById('profile-bio');
  if (artist.bio) {
    bioEl.textContent = artist.bio;
  } else {
    bioSection.style.display = 'none';
  }

  // ---------------------------------------------------------------- //
  // 4. Follower count                                                 //
  // ---------------------------------------------------------------- //
  const followerCountEl = document.getElementById('profile-follower-count');
  if (typeof artist.follower_count === 'number') {
    followerCountEl.textContent =
      `${artist.follower_count} follower${artist.follower_count === 1 ? '' : 's'}`;
  }

  // ---------------------------------------------------------------- //
  // 5. Load releases, posts, and merch in parallel                   //
  // ---------------------------------------------------------------- //
  loadArtistReleases(artist.id);
  loadArtistPosts(artist.id);
  loadArtistMerch(artist.id);

  // ---------------------------------------------------------------- //
  // 6. Show Edit or Follow button depending on session role          //
  // ---------------------------------------------------------------- //
  try {
    // GET /api/auth/me — returns { id, role } for the current session.
    // Silently ignore 401 (unauthenticated visitors).
    const me = await api.get('/auth/me');

    if (me && me.role === 'artist' && me.id === artist.id) {
      // The logged-in user owns this profile — show Edit button.
      document.getElementById('edit-profile-btn').style.display = '';

    } else if (me && me.role === 'fan') {
      // Authenticated fan — wire up the Follow / Unfollow button.
      await initFollowButton(artist.id);
    }
  } catch {
    // Not logged in — neither button shown. This is expected.
  }

  // ---------------------------------------------------------------- //
  // 7. Reveal the populated content, hide the loading skeleton        //
  // ---------------------------------------------------------------- //
  loading.style.display = 'none';
  content.style.display = '';
});

// ------------------------------------------------------------------ //
// Follow / Unfollow                                                    //
// ------------------------------------------------------------------ //

/**
 * Initialise the Follow button for an authenticated fan.
 *
 * 1. Checks GET /api/follows to see if the current fan already follows
 *    this artist.
 * 2. Sets the button text and data-following attribute accordingly.
 * 3. Attaches a click handler that calls POST or DELETE /api/follows.
 * 4. Updates the follower count display optimistically on success.
 *
 * @param {number} artistId - The artist's primary key.
 */
async function initFollowButton(artistId) {
  const btn = document.getElementById('follow-btn');
  const followerCountEl = document.getElementById('profile-follower-count');

  // Determine current follow state.
  let following = false;
  try {
    const data = await api.get('/follows');
    const followed = data.following || [];
    following = followed.some(a => a.id === artistId);
  } catch {
    // If the check fails we default to "not following" and let the
    // click handler produce an error if necessary.
  }

  // Set initial button state.
  btn.dataset.following = String(following);
  btn.textContent = following ? 'Following' : 'Follow';
  btn.classList.toggle('btn-secondary', following);
  btn.classList.toggle('btn-primary', !following);
  btn.style.display = '';

  // Click handler — toggle follow state.
  btn.addEventListener('click', async () => {
    btn.disabled = true;
    const isFollowing = btn.dataset.following === 'true';

    try {
      if (isFollowing) {
        await api.delete(`/follows/${artistId}`);
        btn.dataset.following = 'false';
        btn.textContent = 'Follow';
        btn.classList.replace('btn-secondary', 'btn-primary');
        adjustFollowerCount(followerCountEl, -1);
      } else {
        await api.post('/follows', { artist_id: artistId });
        btn.dataset.following = 'true';
        btn.textContent = 'Following';
        btn.classList.replace('btn-primary', 'btn-secondary');
        adjustFollowerCount(followerCountEl, +1);
      }
    } catch (err) {
      // Show a brief inline error without disrupting the page.
      const msg = err.message || 'Something went wrong. Please try again.';
      showInlineAlert(msg);
    } finally {
      btn.disabled = false;
    }
  });
}

/**
 * Adjust the displayed follower count by `delta` (+1 or -1).
 * Parses the current text, updates it, then re-renders.
 *
 * @param {HTMLElement} el    - The follower count paragraph element.
 * @param {number}      delta - +1 or -1.
 */
function adjustFollowerCount(el, delta) {
  const match = el.textContent.match(/^(\d+)/);
  if (!match) return;
  const newCount = Math.max(0, parseInt(match[1], 10) + delta);
  el.textContent = `${newCount} follower${newCount === 1 ? '' : 's'}`;
}

/**
 * Show a dismissible inline alert just below the follow button.
 * Auto-removes after 4 seconds.
 *
 * @param {string} message - The alert text.
 */
function showInlineAlert(message) {
  // Remove any existing alert first.
  const existing = document.getElementById('follow-alert');
  if (existing) existing.remove();

  const alert = document.createElement('p');
  alert.id = 'follow-alert';
  alert.className = 'alert alert-error';
  alert.style.marginTop = '0.5rem';
  alert.textContent = message;

  const btn = document.getElementById('follow-btn');
  btn.parentNode.insertBefore(alert, btn.nextSibling);
  setTimeout(() => alert.remove(), 4000);
}

/**
 * Fetch this artist's releases via the nested endpoint and render cards.
 * Replaces the "coming soon" placeholder with real release cards.
 *
 * @param {number} artistId - The artist's primary key.
 */
async function loadArtistReleases(artistId) {
  const placeholder = document.getElementById('releases-placeholder');
  const grid        = document.getElementById('releases-grid');

  try {
    const data = await api.get(`/artists/${artistId}/releases?per_page=50`);
    const releases = data.releases || [];

    if (releases.length === 0) {
      // Leave the placeholder text visible.
      return;
    }

    // Hide the placeholder text and render cards.
    if (placeholder) placeholder.style.display = 'none';

    grid.innerHTML = releases.map(r => `
      <div class="card release-card">
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
            ? `<p class="card-meta">${formatDate(r.release_date)}</p>`
            : ''}
        </div>
        <div class="release-actions">
          ${r.streaming_url
            ? `<a
                 href="${escapeHtml(r.streaming_url)}"
                 target="_blank"
                 rel="noopener noreferrer"
                 class="btn btn-primary release-btn"
               >Stream ↗</a>`
            : ''
          }
        </div>
      </div>
    `).join('');
  } catch {
    // Non-critical — profile page still works without releases.
    if (placeholder) {
      placeholder.textContent = 'Could not load releases.';
    }
  }
}

/**
 * Fetch this artist's social posts and render them in the posts section.
 * Replaces the "coming soon" placeholder with real post cards.
 *
 * @param {number} artistId - The artist's primary key.
 */
async function loadArtistPosts(artistId) {
  const placeholder = document.getElementById('posts-placeholder');
  const list        = document.getElementById('posts-list');

  try {
    const data  = await api.get(`/artists/${artistId}/posts?per_page=20`);
    const posts = data.posts || [];

    if (posts.length === 0) {
      return;  // leave placeholder text visible
    }

    if (placeholder) placeholder.style.display = 'none';

    list.innerHTML = posts.map(p => `
      <article class="post-card post-card-profile">
        <div class="post-body">
          <div class="post-header">
            <time
              class="post-time"
              datetime="${escapeHtml(p.created_at)}"
            >${formatRelativeTime(p.created_at)}</time>
          </div>
          <p class="post-text">${escapeHtml(p.body)}</p>
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
  } catch {
    if (placeholder) placeholder.textContent = 'Could not load posts.';
  }
}

/**
 * Format an ISO datetime string as a relative time label.
 *
 * @param {string} iso - ISO 8601 datetime string.
 * @returns {string}   - e.g. "2h ago", "Jun 1, 2024"
 */
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

/**
 * Format an ISO date string to a readable form.
 *
 * @param {string} iso - "YYYY-MM-DD"
 * @returns {string}   - e.g. "Jun 1, 2024"
 */
function formatDate(iso) {
  try {
    return new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, {
      year: 'numeric', month: 'short', day: 'numeric',
    });
  } catch {
    return iso;
  }
}

// ------------------------------------------------------------------ //
// Helpers                                                              //
// ------------------------------------------------------------------ //

/**
 * Fetch this artist's merchandise and render product cards.
 * Replaces the "coming soon" placeholder with real product cards.
 *
 * The Buy button is disabled (catalog-only MVP). Phase 3 wires it
 * to POST /api/orders.
 *
 * @param {number} artistId - The artist's primary key.
 */
async function loadArtistMerch(artistId) {
  const placeholder = document.getElementById('merch-placeholder');
  const grid        = document.getElementById('merch-grid');

  try {
    const data     = await api.get(`/artists/${artistId}/merch?per_page=50`);
    const products = data.products || [];

    if (products.length === 0) return;

    if (placeholder) placeholder.style.display = 'none';

    grid.innerHTML = products.map(p => {
      const inStock = (
        p.inventory_quantity === null || p.inventory_quantity > 0
      );
      return `
        <div class="card merch-card ${inStock ? '' : 'merch-card-oos'}">
          <div class="merch-artwork">
            ${p.image_url
              ? `<img src="${escapeHtml(p.image_url)}"
                      alt="${escapeHtml(p.product_name)}"
                      class="artwork-img" />`
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
              : ''}
          </div>
          <div class="merch-footer">
            <span class="merch-price">$${p.price.toFixed(2)}</span>
            <button
              class="btn btn-primary merch-btn"
              disabled
              title="Purchasing coming soon"
            >Buy</button>
          </div>
        </div>
      `;
    }).join('');
  } catch {
    if (placeholder) placeholder.textContent = 'Could not load merchandise.';
  }
}

/**
 * Show the error panel with a message, hiding loading and content.
 *
 * @param {string} message - Human-readable error message.
 */
function showError(message) {
  document.getElementById('profile-loading').style.display = 'none';
  document.getElementById('profile-content').style.display = 'none';
  const errDiv = document.getElementById('profile-error');
  const errMsg = document.getElementById('profile-error-message');
  errMsg.textContent = message;
  errDiv.style.display = '';
}

/**
 * Derive up to two initials from a display name, e.g. "Jane Doe" → "JD".
 *
 * @param {string} name - The artist's display name.
 * @returns {string}    - 1–2 uppercase characters.
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
 * @param {string} str - Raw string.
 * @returns {string}   - HTML-safe string.
 */
function escapeHtml(str) {
  const div = document.createElement('div');
  div.appendChild(document.createTextNode(String(str)));
  return div.innerHTML;
}
