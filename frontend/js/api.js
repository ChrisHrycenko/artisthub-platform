/**
 * api.js
 *
 * Centralised HTTP client for all ArtistHub frontend requests.
 *
 * RULE: Every HTTP call in the SPA goes through this module.
 *       Never call fetch() directly in a page script.
 *
 * Why?
 *   - API_BASE_URL is defined once here — changing the backend address
 *     requires editing a single line, not hunting across 10 files.
 *   - credentials: "include" is attached to every request automatically,
 *     so Flask-Login session cookies are sent without each page having
 *     to remember to set it.
 *   - The response envelope { status, data } / { status, error } is
 *     unwrapped here, so callers receive resolved data or a thrown Error.
 *
 * Usage:
 *   import { api } from './api.js';   // or just include via <script>
 *
 *   const data = await api.get('/artists');
 *   const artist = await api.post('/auth/artist/login', { email, password });
 */

// ------------------------------------------------------------------ //
// Configuration                                                        //
// ------------------------------------------------------------------ //

// Change this single constant to point to a different backend host.
// In production (served through nginx), this can be an empty string
// because /api/* is proxied to Flask on the same origin.
const API_BASE_URL = 'http://127.0.0.1:5000/api';

// ------------------------------------------------------------------ //
// Core fetch wrapper                                                   //
// ------------------------------------------------------------------ //

/**
 * Make an HTTP request to the ArtistHub API.
 *
 * @param {string} method   - HTTP verb: 'GET', 'POST', 'PUT', 'DELETE'
 * @param {string} path     - API path, e.g. '/artists' (no /api prefix needed)
 * @param {object} [body]   - Optional request body; serialised to JSON automatically.
 * @returns {Promise<any>}  - Resolves with response.data on success.
 * @throws {Error}          - Rejects with the API's error message on failure.
 */
async function request(method, path, body = null) {
  const options = {
    method,
    headers: {
      'Content-Type': 'application/json',
      'Accept':       'application/json',
    },
    // CRITICAL: must be 'include' so Flask-Login session cookies are
    // sent cross-origin during local development (frontend on :5500,
    // backend on :5000). Without this, current_user is always anonymous.
    credentials: 'include',
  };

  if (body !== null) {
    options.body = JSON.stringify(body);
  }

  const response = await fetch(`${API_BASE_URL}${path}`, options);

  // Parse JSON regardless of status code — the envelope always contains
  // either { status: 'success', data: ... } or { status: 'error', error: ... }
  let json;
  try {
    json = await response.json();
  } catch {
    // Non-JSON response (e.g. nginx 502) — surface a generic error.
    throw new Error(`Server returned non-JSON response (HTTP ${response.status})`);
  }

  if (json.status === 'success') {
    return json.data;
  }

  // API returned an error envelope — throw so the caller's catch block handles it.
  throw new Error(json.error || `Request failed with HTTP ${response.status}`);
}

// ------------------------------------------------------------------ //
// Public API surface                                                   //
// ------------------------------------------------------------------ //

/**
 * Convenience wrappers so callers don't need to pass the method string.
 *
 * All return Promises that resolve with the `data` payload or throw Error.
 */
const api = {
  /** GET /api{path} — read a resource, no side effects. */
  get:    (path)        => request('GET',    path),

  /** POST /api{path} — create a new resource. */
  post:   (path, body)  => request('POST',   path, body),

  /** PUT /api{path} — update an existing resource in full. */
  put:    (path, body)  => request('PUT',    path, body),

  /** DELETE /api{path} — remove a resource. */
  delete: (path)        => request('DELETE', path),
};

// Make `api` available globally so scripts loaded via <script src="...">
// can access it without ES module import syntax.
window.api = api;
