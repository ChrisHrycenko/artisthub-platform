/**
 * artist-register.js
 *
 * Handles artist registration form submission on artist-register.html.
 *
 * Flow:
 *   1. Intercept the form submit event.
 *   2. Validate client-side (password length check).
 *   3. POST to /api/auth/artist/register via the api.js wrapper.
 *   4. On success — show confirmation and redirect to artist-dashboard.html.
 *   5. On error  — show the API error message inline.
 *
 * All HTTP calls go through window.api (api.js).
 */

document.addEventListener('DOMContentLoaded', () => {
  const form  = document.getElementById('register-form');
  const alert = document.getElementById('form-alert');
  const btn   = document.getElementById('submit-btn');

  if (!form) return;

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    clearAlert();

    const display_name = form.display_name.value.trim() || null;
    const email        = form.email.value.trim();
    const password     = form.password.value;

    // Client-side guard — catches obvious errors without a round-trip.
    if (!email) {
      showAlert('Email is required.', 'error');
      return;
    }
    if (password.length < 8) {
      showAlert('Password must be at least 8 characters.', 'error');
      return;
    }

    // Disable submit while the request is in flight.
    btn.disabled    = true;
    btn.textContent = 'Creating account…';

    // Build the payload — omit display_name if blank so the API defaults
    // it to the email address.
    const payload = { email, password };
    if (display_name) payload.display_name = display_name;

    try {
      await api.post('/auth/artist/register', payload);

      showAlert(
        'Account created! Redirecting to your dashboard…',
        'success',
      );

      // Redirect after a short delay so the user sees the message.
      setTimeout(() => {
        window.location.href = 'artist-dashboard.html';
      }, 1500);

    } catch (err) {
      // The API returns validation messages as objects (field → [msg])
      // or plain strings. Normalise to a readable sentence.
      const raw = err.message || 'Registration failed. Please try again.';
      showAlert(typeof raw === 'string' ? raw : JSON.stringify(raw), 'error');
      btn.disabled    = false;
      btn.textContent = 'Create Artist Account';
    }
  });

  // ---------------------------------------------------------------- //
  // Helpers                                                            //
  // ---------------------------------------------------------------- //

  /**
   * Display an inline alert message above the form.
   * @param {string} message
   * @param {'success'|'error'} type
   */
  function showAlert(message, type) {
    alert.textContent   = message;
    alert.className     = `alert alert-${type}`;
    alert.style.display = '';
  }

  /** Hide and reset the inline alert. */
  function clearAlert() {
    alert.style.display = 'none';
    alert.textContent   = '';
    alert.className     = 'alert';
  }
});
