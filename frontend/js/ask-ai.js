/**
 * ask-ai.js
 *
 * ArtistHub AI frontend logic for ask-ai.html.
 *
 * Responsibilities:
 *   - Submit the user's question via POST /api/ai/ask (through api.js)
 *   - Display loading state during the request
 *   - Render the answer, grounding status, citations, and
 *     professional-review indicator
 *   - Handle all error states (validation, insufficient evidence,
 *     upstream errors) gracefully
 *
 * All HTTP calls go through window.api from api.js — never fetch() directly.
 */

// ------------------------------------------------------------------ //
// State                                                                //
// ------------------------------------------------------------------ //

/** True while a request is in flight — prevents duplicate submissions. */
let _requesting = false;

// ------------------------------------------------------------------ //
// DOM helpers                                                          //
// ------------------------------------------------------------------ //

function el(id) {
  return document.getElementById(id);
}

function show(id) {
  const e = el(id);
  if (e) { e.classList.add('visible'); e.style.display = ''; }
}

function hide(id) {
  const e = el(id);
  if (e) { e.classList.remove('visible'); e.style.display = 'none'; }
}

// ------------------------------------------------------------------ //
// Public: fill question from topic chip                               //
// ------------------------------------------------------------------ //

/**
 * Fill the question textarea with a suggested topic prompt.
 * Called by the onclick of each topic chip button.
 *
 * @param {string} text - The pre-written question text.
 */
function fillQuestion(text) {
  el('question-input').value = text;
  el('question-input').focus();
}

// ------------------------------------------------------------------ //
// Public: submit question                                              //
// ------------------------------------------------------------------ //

/**
 * Submit the current question to POST /api/ai/ask and render the result.
 *
 * Called by the "Ask" button onclick and the Enter key handler below.
 */
async function askAI() {
  if (_requesting) return;

  const input = el('question-input');
  const question = (input.value || '').trim();

  // Basic client-side validation (mirrors backend 3-char minimum).
  if (question.length < 3) {
    renderError('Please enter a question (at least 3 characters).');
    return;
  }

  _requesting = true;
  setLoading(true);
  clearAnswer();

  try {
    // api.js attaches credentials: 'include' automatically.
    const data = await api.post('/ai/ask', { question });
    renderAnswer(data);
  } catch (err) {
    // api.js throws Error with the API's error message on non-success.
    renderError(err.message || 'ArtistHub AI encountered an unexpected error. Please try again.');
  } finally {
    _requesting = false;
    setLoading(false);
  }
}

// ------------------------------------------------------------------ //
// Keyboard shortcut: Cmd/Ctrl + Enter to submit                       //
// ------------------------------------------------------------------ //

document.addEventListener('DOMContentLoaded', function () {
  const input = el('question-input');
  if (input) {
    input.addEventListener('keydown', function (e) {
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
        askAI();
      }
    });
  }
});

// ------------------------------------------------------------------ //
// Render helpers                                                       //
// ------------------------------------------------------------------ //

/**
 * Show the loading spinner and disable the Ask button.
 *
 * @param {boolean} loading
 */
function setLoading(loading) {
  const btn = el('ask-btn');
  if (loading) {
    show('ai-loading');
    if (btn) { btn.disabled = true; }
  } else {
    hide('ai-loading');
    if (btn) { btn.disabled = false; }
  }
}

/**
 * Clear any previous answer from the panel.
 */
function clearAnswer() {
  el('answer-text').textContent = '';
  el('sources-list').innerHTML = '';
  hide('sources-section');
  hide('review-notice');
  hide('ai-answer-panel');
  el('grounding-badge').textContent = '';
  el('grounding-badge').className = 'ai-badge';
  el('corr-id-display').textContent = '';
}

/**
 * Render a successful AI answer.
 *
 * @param {object} data - The `data` payload from the success envelope.
 *   data.answer                    {string}
 *   data.grounding_status          {string}  'grounded' | 'insufficient_evidence'
 *   data.sources                   {Array}
 *   data.requires_professional_review {boolean}
 *   data.model_id                  {string}
 *   data.correlation_id            {string}
 */
function renderAnswer(data) {
  const panel = el('ai-answer-panel');
  const answerEl = el('answer-text');
  const badge = el('grounding-badge');

  // Answer text
  answerEl.textContent = data.answer || '';

  // Grounding badge
  if (data.grounding_status === 'grounded') {
    badge.textContent = 'Grounded';
    badge.classList.add('ai-badge-grounded');
  } else {
    badge.textContent = 'Limited sources';
    badge.classList.add('ai-badge-insufficient');
  }

  // Professional-review notice
  if (data.requires_professional_review) {
    show('review-notice');
  }

  // Sources / citations
  const sourcesEl = el('sources-section');
  const listEl = el('sources-list');
  const sources = data.sources || [];

  if (sources.length > 0) {
    listEl.innerHTML = '';
    sources.forEach(function (src) {
      const li = document.createElement('li');
      li.className = 'ai-source-item';

      const orgSpan = document.createElement('span');
      orgSpan.className = 'ai-source-org';
      orgSpan.textContent = src.source_org || '';

      const titleSpan = document.createElement('span');
      titleSpan.className = 'ai-source-title';
      titleSpan.textContent = src.document_title || '';

      li.appendChild(orgSpan);
      li.appendChild(titleSpan);

      if (src.source_url) {
        const urlDiv = document.createElement('div');
        urlDiv.className = 'ai-source-url';
        const a = document.createElement('a');
        a.href = src.source_url;
        a.target = '_blank';
        a.rel = 'noopener noreferrer';
        a.textContent = src.source_url;
        urlDiv.appendChild(a);
        li.appendChild(urlDiv);
      }

      listEl.appendChild(li);
    });
    sourcesEl.style.display = '';
  }

  // Correlation ID (for support reference)
  if (data.correlation_id) {
    el('corr-id-display').textContent = 'Reference: ' + data.correlation_id;
  }

  // Show panel
  panel.classList.add('visible');
  panel.style.display = '';
  // Scroll to answer
  panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

/**
 * Render an error state in the answer panel.
 *
 * @param {string} message - Human-readable error description.
 */
function renderError(message) {
  const panel = el('ai-answer-panel');
  const answerEl = el('answer-text');
  const badge = el('grounding-badge');

  answerEl.textContent = message;
  badge.textContent = 'Error';
  badge.classList.add('ai-badge-error');

  panel.classList.add('visible');
  panel.style.display = '';
  panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
}
