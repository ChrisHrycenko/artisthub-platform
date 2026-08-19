/**
 * fan-login.js
 *
 * Drives the fan-login.html page.
 *
 * Flow:
 *   1. On submit, validate that both fields are non-empty.
 *   2. POST /api/auth/fan/login with { email, password }.
 *   3. On success, redirect to fan-dashboard.html.
 *   4. On error, show the API's error message in the alert div.
 *
 * All HTTP calls go through window.api (api.js).
 */

document.addEventListener('DOMContentLoaded', () => {
  const form      = document.getElementById('login-form');
  const alertDiv  = document.getElementById('login-alert');
  const submitBtn = document.getElementById('submit-btn');

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    hideAlert();

    const email    = document.getElementById('email').value.trim();
    const password = document.getElementById('password').value;

    if (!email || !password) {
      showAlert('Please enter your email and password.', 'error');
      return;
    }

    submitBtn.disabled    = true;
    submitBtn.textContent = 'Signing in…';

    try {
      await api.post('/auth/fan/login', { email, password });
      // Success — session cookie is set; redirect to fan dashboard.
      window.location.href = 'fan-dashboard.html';
    } catch (err) {
      showAlert(err.message || 'Login failed. Please try again.', 'error');
    } finally {
      submitBtn.disabled    = false;
      submitBtn.textContent = 'Sign In';
    }
  });

  /**
   * Show the inline alert with the given message and type.
   *
   * @param {string} message - The alert text.
   * @param {'error'|'success'} type - Alert style class suffix.
   */
  function showAlert(message, type) {
    alertDiv.textContent = message;
    alertDiv.className   = `alert alert-${type}`;
    alertDiv.style.display = '';
  }

  /** Hide the inline alert. */
  function hideAlert() {
    alertDiv.style.display = 'none';
    alertDiv.textContent   = '';
  }
});
