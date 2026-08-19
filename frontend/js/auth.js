/**
 * auth.js
 *
 * Session state helpers for ArtistHub pages.
 *
 * On every page load, checkSession() calls GET /api/auth/me to determine
 * whether the current user is logged in. Based on the result:
 *   - The nav bar is updated (guest links vs authed links)
 *   - Pages that require auth redirect to the appropriate login page
 *
 * This file is a stub for Phase 1. The full register/login/logout
 * functions will be implemented in Phase 2 (auth Blueprint).
 *
 * RULE: All session-related logic lives here — not duplicated in page scripts.
 */

/**
 * Query the backend for the current session state.
 *
 * Returns the user object { id, email, role, ... } if logged in,
 * or null if the session is anonymous.
 *
 * @returns {Promise<object|null>}
 */
async function checkSession() {
  try {
    // GET /api/auth/me — implemented in Phase 2.
    // Returns 401 if unauthenticated; api.js throws, we catch below.
    const user = await api.get('/auth/me');
    return user;
  } catch {
    // 401 or network error — treat as not logged in.
    return null;
  }
}

/**
 * Update navigation link visibility based on session state.
 *
 * Shows #nav-guest when logged out, #nav-authed when logged in.
 * Also sets the dashboard link href based on user role.
 *
 * @param {object|null} user - User object from checkSession(), or null.
 */
function updateNav(user) {
  const navGuest  = document.getElementById('nav-guest');
  const navAuthed = document.getElementById('nav-authed');
  const navDash   = document.getElementById('nav-dashboard');

  if (!navGuest || !navAuthed) return; // Nav elements may not exist on all pages.

  if (user) {
    navGuest.style.display  = 'none';
    navAuthed.style.display = '';
    // Point dashboard link to the correct page for this user's role.
    if (navDash) {
      navDash.href = user.role === 'artist'
        ? 'artist-dashboard.html'
        : 'fan-dashboard.html';
    }
  } else {
    navGuest.style.display  = '';
    navAuthed.style.display = 'none';
  }
}

/**
 * Wire up the Sign Out button in the nav.
 *
 * The actual POST /api/auth/logout endpoint is implemented in Phase 2.
 */
function bindLogout() {
  const btn = document.getElementById('nav-logout');
  if (!btn) return;

  btn.addEventListener('click', async (e) => {
    e.preventDefault();
    try {
      await api.post('/auth/logout');
    } catch {
      // Even if the request fails, clear local state and redirect.
    }
    window.location.href = 'index.html';
  });
}

// ------------------------------------------------------------------ //
// Auto-run on every page that includes auth.js                        //
// ------------------------------------------------------------------ //
document.addEventListener('DOMContentLoaded', async () => {
  const user = await checkSession();
  updateNav(user);
  bindLogout();
});
