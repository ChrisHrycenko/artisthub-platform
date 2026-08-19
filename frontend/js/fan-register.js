/**
 * fan-register.js
 *
 * Handles fan registration form submission on fan-register.html.
 *
 * Flow:
 *   1. Intercept the form submit event.
 *   2. Validate client-side (HTML5 + password length check).
 *   3. POST to /api/fans/register via the api.js wrapper.
 *   4. On success — show confirmation and redirect to fan-login.html.
 *   5. On error  — show the API error message inline.
 */

document.addEventListener('DOMContentLoaded', () => {
  const form  = document.getElementById('register-form');
  const alert = document.getElementById('form-alert');

  if (!form) return;

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    clearAlert();

    const username = form.username.value.trim();
    const email    = form.email.value.trim();
    const password = form.password.value;

    // Client-side guard — catches obvious errors without a round-trip.
    if (password.length < 8) {
      showAlert('Password must be at least 8 characters.', 'error');
      return;
    }

    // Disable submit while the request is in flight.
    const btn = form.querySelector('button[type="submit"]');
    btn.disabled = true;
    btn.textContent = 'Creating account…';

    try {
      await api.post('/fans/register', { username, email, password });

      showAlert(
        `Welcome, ${username}! Redirecting to login…`,
        'success',
      );

      // Redirect after a short delay so the user sees the message.
      setTimeout(() => {
        window.location.href = 'fan-login.html';
      }, 1500);

    } catch (err) {
      showAlert(err.message || 'Registration failed. Please try again.', 'error');
      btn.disabled = false;
      btn.textContent = 'Create Account';
    }
  });

  // ---------------------------------------------------------------- //
  // Helpers                                                            //
  // ---------------------------------------------------------------- //

  /**
   * Display an inline alert message above the form.
   *
   * @param {string} message  - The text to display.
   * @param {'success'|'error'} type - Controls CSS class applied.
   */
  function showAlert(message, type) {
    alert.textContent = message;
    alert.className   = `alert alert-${type}`;
    alert.style.display = '';
  }

  /** Hide and reset the inline alert. */
  function clearAlert() {
    alert.style.display = 'none';
    alert.textContent   = '';
    alert.className     = 'alert';
  }
});
