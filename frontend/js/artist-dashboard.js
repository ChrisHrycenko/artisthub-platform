/**
 * artist-dashboard.js
 *
 * Drives the artist-dashboard.html page.
 *
 * Flow:
 *   1. Call GET /api/auth/me — redirect to login if not authenticated
 *      as an artist.
 *   2. Fetch the artist's full profile (GET /api/artists/<id>).
 *   3. Populate stats (follower count, release count, post count, merch count).
 *   4. Pre-fill the Edit Profile form with current values.
 *   5. Handle profile form submission (PUT /api/artists/<id>).
 *   6. Handle new-post form submission (POST /api/posts).
 *   7. Render the artist's 10 most recent posts.
 *   8. Handle add-release form submission (POST /api/releases).
 *   9. Render the artist's releases with delete buttons.
 *  10. Handle add-merch form submission (POST /api/merch).
 *  11. Render the artist's merch products with delete buttons.
 *
 * All HTTP calls go through window.api (api.js).
 */

document.addEventListener('DOMContentLoaded', async () => {
  const loading = document.getElementById('dashboard-loading');
  const errDiv  = document.getElementById('dashboard-error');
  const content = document.getElementById('dashboard-content');

  // ---------------------------------------------------------------- //
  // 1. Auth check — must be an artist session                         //
  // ---------------------------------------------------------------- //
  let me;
  try {
    me = await api.get('/auth/me');
  } catch {
    me = null;
  }

  if (!me || me.role !== 'artist') {
    loading.style.display = 'none';
    errDiv.style.display  = '';
    return;
  }

  // ---------------------------------------------------------------- //
  // 2. Fetch full artist profile                                      //
  // ---------------------------------------------------------------- //
  let artist;
  try {
    const data = await api.get(`/artists/${me.id}`);
    artist = data.artist;
  } catch {
    loading.style.display = 'none';
    errDiv.style.display  = '';
    return;
  }

  // ---------------------------------------------------------------- //
  // 3. Populate hero band                                             //
  // ---------------------------------------------------------------- //
  const avatarEl = document.getElementById('dash-avatar');
  avatarEl.textContent = initials(artist.display_name);

  document.getElementById('dash-display-name').textContent =
    artist.display_name;

  const subtitleParts = [artist.genre, artist.location].filter(Boolean);
  document.getElementById('dash-genre-location').textContent =
    subtitleParts.join(' · ');

  const fc = artist.follower_count || 0;
  document.getElementById('dash-follower-count').textContent =
    `${fc} follower${fc === 1 ? '' : 's'}`;

  document.getElementById('dash-view-profile-btn').href =
    `artist-profile.html?id=${artist.id}`;

  // Wire the Analytics nav link — only meaningful once we know the artist id.
  const analyticsNavLink = document.getElementById('analytics-nav-link');
  if (analyticsNavLink) {
    analyticsNavLink.href = `artist-analytics.html?id=${artist.id}`;
  }

  // ---------------------------------------------------------------- //
  // 4. Fetch stats in parallel                                        //
  // ---------------------------------------------------------------- //
  document.getElementById('stat-followers').textContent =
    artist.follower_count;

  const [relData, postData, merchData] = await Promise.allSettled([
    api.get(`/artists/${artist.id}/releases?per_page=1`),
    api.get(`/artists/${artist.id}/posts?per_page=1`),
    api.get(`/artists/${artist.id}/merch?per_page=1`),
  ]);

  if (relData.status === 'fulfilled') {
    document.getElementById('stat-releases').textContent =
      relData.value.total ?? '—';
  }
  if (postData.status === 'fulfilled') {
    document.getElementById('stat-posts').textContent =
      postData.value.total ?? '—';
  }
  if (merchData.status === 'fulfilled') {
    document.getElementById('stat-merch').textContent =
      merchData.value.total ?? '—';
  }

  // ---------------------------------------------------------------- //
  // 5. Pre-fill the Edit Profile form                                 //
  // ---------------------------------------------------------------- //
  setValue('display_name',       artist.display_name);
  setValue('genre',              artist.genre);
  setValue('location',           artist.location);
  setValue('bio',                artist.bio);
  setValue('profile_image_url',  artist.profile_image_url);

  const profileForm    = document.getElementById('profile-form');
  const profileAlert   = document.getElementById('profile-alert');
  const profileSaveBtn = document.getElementById('profile-save-btn');

  profileForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    hideAlert(profileAlert);
    profileSaveBtn.disabled    = true;
    profileSaveBtn.textContent = 'Saving…';

    const payload = {
      display_name:      getValue('display_name'),
      genre:             getValue('genre') || null,
      location:          getValue('location') || null,
      bio:               getValue('bio') || null,
      profile_image_url: getValue('profile_image_url') || null,
    };

    try {
      const updated = await api.put(`/artists/${artist.id}`, payload);
      artist = updated.artist;
      // Refresh hero with new values.
      document.getElementById('dash-display-name').textContent =
        artist.display_name;
      showAlert(profileAlert, 'Profile updated!', 'success');
    } catch (err) {
      showAlert(profileAlert, err.message || 'Update failed.', 'error');
    } finally {
      profileSaveBtn.disabled    = false;
      profileSaveBtn.textContent = 'Save Changes';
    }
  });

  // ---------------------------------------------------------------- //
  // 6. New post form                                                  //
  // ---------------------------------------------------------------- //
  const postForm      = document.getElementById('post-form');
  const postAlert     = document.getElementById('post-alert');
  const postSubmitBtn = document.getElementById('post-submit-btn');
  const postBodyEl    = document.getElementById('post-body');
  const charCountEl   = document.getElementById('post-char-count');

  postBodyEl.addEventListener('input', () => {
    charCountEl.textContent = postBodyEl.value.length;
  });

  postForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    hideAlert(postAlert);
    postSubmitBtn.disabled    = true;
    postSubmitBtn.textContent = 'Publishing…';

    const body      = postBodyEl.value.trim();
    const imageUrl  = getValue('post-image') || null;

    if (!body) {
      showAlert(postAlert, 'Post body cannot be empty.', 'error');
      postSubmitBtn.disabled    = false;
      postSubmitBtn.textContent = 'Publish Post';
      return;
    }

    try {
      await api.post('/posts', {
        body,
        ...(imageUrl ? { image_url: imageUrl } : {}),
      });
      showAlert(postAlert, 'Post published!', 'success');
      postBodyEl.value        = '';
      charCountEl.textContent = '0';
      setValue('post-image', '');
      // Reload posts list.
      await loadRecentPosts(artist.id);
    } catch (err) {
      showAlert(postAlert, err.message || 'Publish failed.', 'error');
    } finally {
      postSubmitBtn.disabled    = false;
      postSubmitBtn.textContent = 'Publish Post';
    }
  });

  // ---------------------------------------------------------------- //
  // 7. Load and render recent posts                                   //
  // ---------------------------------------------------------------- //
  await loadRecentPosts(artist.id);

  // ---------------------------------------------------------------- //
  // 8. Add-release form                                               //
  // ---------------------------------------------------------------- //
  const releaseForm      = document.getElementById('release-form');
  const releaseAlert     = document.getElementById('release-alert');
  const releaseSubmitBtn = document.getElementById('release-submit-btn');

  releaseForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    hideAlert(releaseAlert);
    releaseSubmitBtn.disabled    = true;
    releaseSubmitBtn.textContent = 'Adding…';

    const title = getValue('release-title');
    if (!title) {
      showAlert(releaseAlert, 'Title is required.', 'error');
      releaseSubmitBtn.disabled    = false;
      releaseSubmitBtn.textContent = 'Add Release';
      return;
    }

    const payload = {
      title,
      release_type: getValue('release-type') || 'Single',
    };
    const genre       = getValue('release-genre');
    const releaseDate = getValue('release-date');
    const streaming   = getValue('release-streaming-url');
    const artwork     = getValue('release-artwork-url');
    if (genre)       payload.genre          = genre;
    if (releaseDate) payload.release_date   = releaseDate;
    if (streaming)   payload.streaming_url  = streaming;
    if (artwork)     payload.artwork_url    = artwork;

    try {
      await api.post('/releases', payload);
      showAlert(releaseAlert, 'Release added!', 'success');
      releaseForm.reset();
      // Refresh list and stat counter.
      await loadReleases(artist.id);
      const relCount = await api.get(`/artists/${artist.id}/releases?per_page=1`);
      document.getElementById('stat-releases').textContent =
        relCount.total ?? '—';
    } catch (err) {
      showAlert(releaseAlert, err.message || 'Failed to add release.', 'error');
    } finally {
      releaseSubmitBtn.disabled    = false;
      releaseSubmitBtn.textContent = 'Add Release';
    }
  });

  // ---------------------------------------------------------------- //
  // 9. Load existing releases                                         //
  // ---------------------------------------------------------------- //
  await loadReleases(artist.id);

  // ---------------------------------------------------------------- //
  // 10. Add-merch form                                                //
  // ---------------------------------------------------------------- //
  const merchForm      = document.getElementById('merch-form');
  const merchAlert     = document.getElementById('merch-alert');
  const merchSubmitBtn = document.getElementById('merch-submit-btn');

  merchForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    hideAlert(merchAlert);
    merchSubmitBtn.disabled    = true;
    merchSubmitBtn.textContent = 'Adding…';

    const productName = getValue('merch-name');
    const priceRaw    = getValue('merch-price');

    if (!productName) {
      showAlert(merchAlert, 'Product name is required.', 'error');
      merchSubmitBtn.disabled    = false;
      merchSubmitBtn.textContent = 'Add Product';
      return;
    }
    if (priceRaw === '' || isNaN(parseFloat(priceRaw)) || parseFloat(priceRaw) < 0) {
      showAlert(merchAlert, 'A valid price (0 or greater) is required.', 'error');
      merchSubmitBtn.disabled    = false;
      merchSubmitBtn.textContent = 'Add Product';
      return;
    }

    const payload = {
      product_name: productName,
      price:        parseFloat(priceRaw),
    };
    const description = getValue('merch-description');
    const imageUrl    = getValue('merch-image-url');
    const invRaw      = getValue('merch-inventory');
    if (description) payload.description        = description;
    if (imageUrl)    payload.image_url          = imageUrl;
    if (invRaw !== '') {
      const qty = parseInt(invRaw, 10);
      if (!isNaN(qty) && qty >= 0) payload.inventory_quantity = qty;
    }

    try {
      await api.post('/merch', payload);
      showAlert(merchAlert, 'Product added!', 'success');
      merchForm.reset();
      // Refresh list and stat counter.
      await loadMerch(artist.id);
      const mc = await api.get(`/artists/${artist.id}/merch?per_page=1`);
      document.getElementById('stat-merch').textContent = mc.total ?? '—';
    } catch (err) {
      showAlert(merchAlert, err.message || 'Failed to add product.', 'error');
    } finally {
      merchSubmitBtn.disabled    = false;
      merchSubmitBtn.textContent = 'Add Product';
    }
  });

  // ---------------------------------------------------------------- //
  // 11. Load existing merch                                           //
  // ---------------------------------------------------------------- //
  await loadMerch(artist.id);

  // Reveal the dashboard.
  loading.style.display = 'none';
  content.style.display = '';
});

// ------------------------------------------------------------------ //
// Helpers                                                              //
// ------------------------------------------------------------------ //

// ------------------------------------------------------------------ //
// Release list                                                         //
// ------------------------------------------------------------------ //

/**
 * Fetch and render all of the artist's releases, each with a Delete button.
 *
 * @param {number} artistId
 */
async function loadReleases(artistId) {
  const list       = document.getElementById('releases-list');
  const emptyNote  = document.getElementById('releases-empty');

  try {
    const data     = await api.get(`/artists/${artistId}/releases?per_page=200`);
    const releases = data.releases || [];

    if (releases.length === 0) {
      list.innerHTML          = '';
      emptyNote.style.display = '';
      return;
    }

    emptyNote.style.display = 'none';
    list.innerHTML = releases.map(r => `
      <div class="admin-row" data-release-id="${r.id}">
        <div class="admin-row-info">
          <strong>${escapeHtml(r.title)}</strong>
          <span class="card-tag">${escapeHtml(r.release_type)}</span>
          ${r.genre ? `<span class="card-meta">${escapeHtml(r.genre)}</span>` : ''}
          ${r.release_date ? `<span class="card-meta">${escapeHtml(r.release_date)}</span>` : ''}
        </div>
        <button
          class="btn btn-danger btn-sm release-delete-btn"
          data-id="${r.id}"
          aria-label="Delete release ${escapeHtml(r.title)}"
        >Delete</button>
      </div>
    `).join('');

    // Attach delete handlers.
    list.querySelectorAll('.release-delete-btn').forEach(btn => {
      btn.addEventListener('click', async () => {
        if (!confirm(`Delete "${btn.closest('.admin-row').querySelector('strong').textContent}"?`)) {
          return;
        }
        btn.disabled    = true;
        btn.textContent = 'Deleting…';
        try {
          await api.delete(`/releases/${btn.dataset.id}`);
          btn.closest('.admin-row').remove();
          // Show empty state if the list is now empty.
          if (!list.querySelector('.admin-row')) {
            emptyNote.style.display = '';
          }
          // Update stat counter.
          const relCount = await api.get(`/artists/${artistId}/releases?per_page=1`);
          document.getElementById('stat-releases').textContent =
            relCount.total ?? '—';
        } catch (err) {
          btn.disabled    = false;
          btn.textContent = 'Delete';
          alert(err.message || 'Delete failed.');
        }
      });
    });
  } catch {
    list.innerHTML = '<p class="muted">Could not load releases.</p>';
  }
}

// ------------------------------------------------------------------ //
// Merch list                                                           //
// ------------------------------------------------------------------ //

/**
 * Fetch and render all of the artist's merch products, each with a Delete button.
 *
 * @param {number} artistId
 */
async function loadMerch(artistId) {
  const list      = document.getElementById('merch-list');
  const emptyNote = document.getElementById('merch-empty');

  try {
    const data     = await api.get(`/artists/${artistId}/merch?per_page=200`);
    const products = data.products || [];

    if (products.length === 0) {
      list.innerHTML          = '';
      emptyNote.style.display = '';
      return;
    }

    emptyNote.style.display = 'none';
    list.innerHTML = products.map(p => `
      <div class="admin-row" data-merch-id="${p.id}">
        <div class="admin-row-info">
          <strong>${escapeHtml(p.product_name)}</strong>
          <span class="card-meta">$${parseFloat(p.price).toFixed(2)}</span>
          ${p.inventory_quantity !== null && p.inventory_quantity !== undefined
            ? `<span class="card-tag">${p.inventory_quantity} in stock</span>`
            : '<span class="card-tag">Unlimited</span>'}
        </div>
        <button
          class="btn btn-danger btn-sm merch-delete-btn"
          data-id="${p.id}"
          aria-label="Delete product ${escapeHtml(p.product_name)}"
        >Delete</button>
      </div>
    `).join('');

    // Attach delete handlers.
    list.querySelectorAll('.merch-delete-btn').forEach(btn => {
      btn.addEventListener('click', async () => {
        if (!confirm(`Delete "${btn.closest('.admin-row').querySelector('strong').textContent}"?`)) {
          return;
        }
        btn.disabled    = true;
        btn.textContent = 'Deleting…';
        try {
          await api.delete(`/merch/${btn.dataset.id}`);
          btn.closest('.admin-row').remove();
          if (!list.querySelector('.admin-row')) {
            emptyNote.style.display = '';
          }
          // Update stat counter.
          const mc = await api.get(`/artists/${artistId}/merch?per_page=1`);
          document.getElementById('stat-merch').textContent = mc.total ?? '—';
        } catch (err) {
          btn.disabled    = false;
          btn.textContent = 'Delete';
          alert(err.message || 'Delete failed.');
        }
      });
    });
  } catch {
    list.innerHTML = '<p class="muted">Could not load merchandise.</p>';
  }
}

// ------------------------------------------------------------------ //
// Post list                                                            //
// ------------------------------------------------------------------ //

/**
 * Fetch and render the artist's 10 most recent posts.
 *
 * @param {number} artistId
 */
async function loadRecentPosts(artistId) {
  const list      = document.getElementById('posts-list');
  const emptyNote = document.getElementById('posts-empty');

  try {
    const data  = await api.get(`/artists/${artistId}/posts?per_page=10`);
    const posts = data.posts || [];

    if (posts.length === 0) {
      list.innerHTML         = '';
      emptyNote.style.display = '';
      return;
    }

    emptyNote.style.display = 'none';
    list.innerHTML = posts.map(p => `
      <article class="post-card post-card-profile">
        <div class="post-body">
          <div class="post-header">
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
  } catch {
    list.innerHTML = '<p class="muted">Could not load posts.</p>';
  }
}

/** Get the trimmed value of an input / textarea by id. */
function getValue(id) {
  const el = document.getElementById(id);
  return el ? el.value.trim() : '';
}

/** Set the value of an input / textarea by id (skips null/undefined). */
function setValue(id, value) {
  const el = document.getElementById(id);
  if (el && value != null) el.value = value;
}

/** Show an inline alert. */
function showAlert(el, message, type) {
  el.textContent   = message;
  el.className     = `alert alert-${type}`;
  el.style.display = '';
}

/** Hide an inline alert. */
function hideAlert(el) {
  el.style.display = 'none';
  el.textContent   = '';
}

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
