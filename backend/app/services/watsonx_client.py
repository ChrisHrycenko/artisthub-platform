"""
services/watsonx_client.py

IBM watsonx.ai HTTP client for ArtistHub Phase 9A.

Provides a thin, mockable abstraction over the IBM watsonx.ai REST API.
All IBM API calls MUST go through this module — never scatter raw HTTP
calls to IBM endpoints across Flask routes or other services.

Configuration (all from environment variables — no secrets hardcoded):
    WATSONX_API_URL       Base URL for the watsonx.ai instance.
                          Default: https://us-south.ml.cloud.ibm.com
    WATSONX_API_KEY       IBM Cloud IAM API key (secret — never in ConfigMap).
    WATSONX_PROJECT_ID    watsonx.ai project ID (or space ID if using a space).
    WATSONX_MODEL_ID      Foundation model to use for generation.
                          Default: ibm/granite-13b-instruct-v2
    WATSONX_MAX_TOKENS    Maximum new tokens per generation response.
                          Default: 512
    WATSONX_TEMPERATURE   Sampling temperature.  Default: 0.1 (near-deterministic
                          for factual Q&A; avoids hallucination drift).
    WATSONX_TIMEOUT       HTTP request timeout in seconds.  Default: 30
    WATSONX_MAX_RETRIES   Maximum retry attempts on transient errors.  Default: 2

IAM token lifecycle:
    IBM Cloud IAM tokens expire after 60 minutes. The client caches the
    token and refreshes it proactively 5 minutes before expiry so live
    requests are never blocked by a token exchange.

Testability:
    When WATSONX_API_KEY is absent (e.g. in CI), generate() raises
    WatsonxUnavailableError immediately rather than attempting a network
    call. Tests mock this class or set WATSONX_MOCK=true to activate a
    deterministic stub response without network access.

Error taxonomy:
    WatsonxAuthError         — IAM token acquisition failed (bad key)
    WatsonxTimeoutError      — Request exceeded WATSONX_TIMEOUT
    WatsonxUpstreamError     — 5xx / unexpected HTTP response from IBM
    WatsonxUnavailableError  — Credentials absent; client not configured
"""

import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
# Optional dependency — requests                                       #
# ------------------------------------------------------------------ #
try:
    import requests as _requests
    _REQUESTS_AVAILABLE = True
except ImportError:  # pragma: no cover
    _REQUESTS_AVAILABLE = False


# ------------------------------------------------------------------ #
# Custom exceptions                                                    #
# ------------------------------------------------------------------ #

class WatsonxError(Exception):
    """Base class for all watsonx client errors."""


class WatsonxAuthError(WatsonxError):
    """IAM token acquisition or validation failed."""


class WatsonxTimeoutError(WatsonxError):
    """Request to watsonx.ai timed out."""


class WatsonxUpstreamError(WatsonxError):
    """watsonx.ai returned an unexpected HTTP error (4xx/5xx)."""


class WatsonxUnavailableError(WatsonxError):
    """
    watsonx client is not configured (credentials absent).

    Raised when WATSONX_API_KEY is not set so the application can
    degrade gracefully rather than attempting a network call.
    """


# ------------------------------------------------------------------ #
# Constants                                                            #
# ------------------------------------------------------------------ #

_IAM_TOKEN_URL = "https://iam.cloud.ibm.com/identity/token"
# Refresh the IAM token 5 minutes before it expires (tokens last 60 min).
_TOKEN_REFRESH_BUFFER_SECONDS = 300


# ------------------------------------------------------------------ #
# WatsonxClient                                                        #
# ------------------------------------------------------------------ #

class WatsonxClient:
    """
    IBM watsonx.ai REST client for ArtistHub.

    One instance is created per application process (singleton via
    get_watsonx_client()). The instance is stateful only for the
    IAM access token cache; all other operations are stateless.

    Usage:
        client = get_watsonx_client()
        result = client.generate(
            prompt="...",
            correlation_id="abc-123",
        )
        # result.generated_text, result.stop_reason, result.model_id

    When WATSONX_MOCK=true the client returns a canned stub response
    without any network activity (useful for unit tests and CI).
    """

    def __init__(self) -> None:
        """
        Read configuration from environment variables.

        Raises WatsonxUnavailableError if WATSONX_API_KEY is absent and
        WATSONX_MOCK is not set to "true".
        """
        self._api_url: str = os.environ.get(
            "WATSONX_API_URL", "https://us-south.ml.cloud.ibm.com"
        ).rstrip("/")
        self._api_key: str = os.environ.get("WATSONX_API_KEY", "")
        self._project_id: str = os.environ.get("WATSONX_PROJECT_ID", "")
        self._model_id: str = os.environ.get(
            "WATSONX_MODEL_ID", "ibm/granite-13b-instruct-v2"
        )
        self._max_tokens: int = int(
            os.environ.get("WATSONX_MAX_TOKENS", "512")
        )
        self._temperature: float = float(
            os.environ.get("WATSONX_TEMPERATURE", "0.1")
        )
        self._timeout: float = float(
            os.environ.get("WATSONX_TIMEOUT", "30")
        )
        self._max_retries: int = int(
            os.environ.get("WATSONX_MAX_RETRIES", "2")
        )
        self._mock: bool = (
            os.environ.get("WATSONX_MOCK", "false").lower() == "true"
        )

        # Cached IAM access token and its expiry epoch.
        self._access_token: Optional[str] = None
        self._token_expiry: float = 0.0

        if not self._mock and not self._api_key:
            raise WatsonxUnavailableError(
                "WATSONX_API_KEY is not set. "
                "Set WATSONX_MOCK=true for local development without "
                "IBM credentials."
            )

        logger.info(
            "WatsonxClient initialised | model=%s project=%s mock=%s",
            self._model_id,
            self._project_id or "(not set)",
            self._mock,
        )

    # ---------------------------------------------------------------- #
    # IAM token management                                               #
    # ---------------------------------------------------------------- #

    def _refresh_token_if_needed(self) -> str:
        """
        Return a valid IAM access token, refreshing proactively if near expiry.

        Raises:
            WatsonxAuthError: if the IAM token request fails.
        """
        now = time.time()
        if self._access_token and now < (
            self._token_expiry - _TOKEN_REFRESH_BUFFER_SECONDS
        ):
            return self._access_token

        logger.debug("Refreshing IBM IAM access token")
        try:
            resp = _requests.post(
                _IAM_TOKEN_URL,
                data={
                    "grant_type": "urn:ibm:params:oauth:grant-type:apikey",
                    "apikey": self._api_key,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=15,
            )
            resp.raise_for_status()
        except _requests.exceptions.Timeout as exc:
            raise WatsonxTimeoutError(
                "IAM token request timed out."
            ) from exc
        except _requests.exceptions.HTTPError as exc:
            raise WatsonxAuthError(
                f"IAM token request failed: HTTP {exc.response.status_code}"
            ) from exc
        except _requests.exceptions.RequestException as exc:
            raise WatsonxAuthError(
                f"IAM token request error: {exc}"
            ) from exc

        payload = resp.json()
        self._access_token = payload["access_token"]
        # IBM IAM tokens expire in 3600s; record absolute expiry time.
        self._token_expiry = now + int(payload.get("expires_in", 3600))
        logger.debug(
            "IAM token refreshed | expires_in=%s",
            payload.get("expires_in"),
        )
        return self._access_token

    # ---------------------------------------------------------------- #
    # Text generation                                                    #
    # ---------------------------------------------------------------- #

    def generate(
        self,
        prompt: str,
        correlation_id: Optional[str] = None,
        max_new_tokens: Optional[int] = None,
    ) -> "GenerationResult":
        """
        Submit a text generation request to watsonx.ai.

        Args:
            prompt:         The fully assembled prompt string.
            correlation_id: Optional trace ID forwarded to IBM API and logs.
            max_new_tokens: Override the configured WATSONX_MAX_TOKENS for
                            this call only.

        Returns:
            GenerationResult with generated_text, stop_reason, model_id,
            input_token_count, generated_token_count.

        Raises:
            WatsonxUnavailableError: credentials absent or MOCK not set.
            WatsonxAuthError:        IAM authentication failed.
            WatsonxTimeoutError:     Request timed out.
            WatsonxUpstreamError:    Non-2xx from IBM API.
        """
        if self._mock:
            return self._mock_response(prompt)

        tokens = max_new_tokens or self._max_tokens

        body = {
            "model_id": self._model_id,
            "input": prompt,
            "parameters": {
                "decoding_method": "greedy",
                "max_new_tokens": tokens,
                "temperature": self._temperature,
                "stop_sequences": [],
            },
            "project_id": self._project_id,
        }

        last_exc: Optional[Exception] = None
        for attempt in range(1, self._max_retries + 2):
            try:
                token = self._refresh_token_if_needed()
                headers = {
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                }
                if correlation_id:
                    headers["X-Correlation-ID"] = correlation_id

                url = (
                    f"{self._api_url}/ml/v1/text/generation"
                    f"?version=2023-05-29"
                )
                resp = _requests.post(
                    url,
                    json=body,
                    headers=headers,
                    timeout=self._timeout,
                )
                resp.raise_for_status()
                return GenerationResult.from_response(resp.json(), self._model_id)

            except _requests.exceptions.Timeout as exc:
                last_exc = exc
                logger.warning(
                    "watsonx.ai timeout | attempt=%d/%d corr=%s",
                    attempt, self._max_retries + 1, correlation_id,
                )
            except _requests.exceptions.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else 0
                # Do not retry 4xx (client errors).
                if status and 400 <= status < 500:
                    raise WatsonxUpstreamError(
                        f"watsonx.ai client error: HTTP {status}"
                    ) from exc
                last_exc = exc
                logger.warning(
                    "watsonx.ai HTTP error | attempt=%d/%d status=%s corr=%s",
                    attempt, self._max_retries + 1, status, correlation_id,
                )
            except _requests.exceptions.RequestException as exc:
                last_exc = exc
                logger.warning(
                    "watsonx.ai request error | attempt=%d/%d corr=%s err=%s",
                    attempt, self._max_retries + 1, correlation_id, exc,
                )

            if attempt <= self._max_retries:
                # Brief pause before retry (exponential: 1s, 2s).
                time.sleep(2 ** (attempt - 1))

        # All attempts exhausted.
        if isinstance(last_exc, _requests.exceptions.Timeout):
            raise WatsonxTimeoutError(
                f"watsonx.ai timed out after {self._max_retries + 1} attempts."
            ) from last_exc
        raise WatsonxUpstreamError(
            f"watsonx.ai failed after {self._max_retries + 1} attempts: "
            f"{last_exc}"
        ) from last_exc

    def _mock_response(self, prompt: str) -> "GenerationResult":
        """
        Return a deterministic stub response for testing / local dev.

        The stub acknowledges the question and returns a canned educational
        note.  It is distinguishable from a real response by model_id.
        """
        return GenerationResult(
            generated_text=(
                "This is a mock ArtistHub AI response for local development. "
                "Set WATSONX_MOCK=false and provide a valid WATSONX_API_KEY "
                "to receive real responses."
            ),
            stop_reason="mock",
            model_id="mock/stub",
            input_token_count=0,
            generated_token_count=0,
        )

    @property
    def model_id(self) -> str:
        """Return the configured (or mock) model ID."""
        return self._model_id if not self._mock else "mock/stub"


# ------------------------------------------------------------------ #
# GenerationResult                                                     #
# ------------------------------------------------------------------ #

class GenerationResult:
    """
    Value object returned by WatsonxClient.generate().

    Attributes:
        generated_text:        The model's text output.
        stop_reason:           Why generation stopped (e.g. 'eos_token', 'max_tokens').
        model_id:              Model that produced the result.
        input_token_count:     Number of prompt tokens consumed.
        generated_token_count: Number of new tokens generated.
    """

    __slots__ = (
        "generated_text",
        "stop_reason",
        "model_id",
        "input_token_count",
        "generated_token_count",
    )

    def __init__(
        self,
        generated_text: str,
        stop_reason: str,
        model_id: str,
        input_token_count: int,
        generated_token_count: int,
    ) -> None:
        """Initialise a GenerationResult."""
        self.generated_text = generated_text
        self.stop_reason = stop_reason
        self.model_id = model_id
        self.input_token_count = input_token_count
        self.generated_token_count = generated_token_count

    @classmethod
    def from_response(cls, body: dict, model_id: str) -> "GenerationResult":
        """
        Parse a watsonx.ai generation response body into a GenerationResult.

        Expected IBM API shape (v1/text/generation):
        {
          "results": [{
            "generated_text": "...",
            "stop_reason": "eos_token",
            "input_token_count": 120,
            "generated_token_count": 48
          }],
          "model_id": "ibm/granite-13b-instruct-v2"
        }
        """
        results = body.get("results", [{}])
        first = results[0] if results else {}
        return cls(
            generated_text=first.get("generated_text", ""),
            stop_reason=first.get("stop_reason", "unknown"),
            model_id=body.get("model_id", model_id),
            input_token_count=int(first.get("input_token_count", 0)),
            generated_token_count=int(first.get("generated_token_count", 0)),
        )


# ------------------------------------------------------------------ #
# Module-level singleton                                               #
# ------------------------------------------------------------------ #

_client: Optional[WatsonxClient] = None


def get_watsonx_client() -> WatsonxClient:
    """
    Return the module-level WatsonxClient singleton.

    Creates the instance on first call.  Uses a simple module-level
    variable rather than Flask's g so the client can be shared across
    requests without re-authenticating each time.

    Raises WatsonxUnavailableError if credentials are not configured
    and WATSONX_MOCK is not set.
    """
    global _client
    if _client is None:
        _client = WatsonxClient()
    return _client


def reset_client() -> None:
    """
    Reset the singleton (for testing only).

    Call this in test teardown to allow a fresh WatsonxClient to be
    constructed with different environment variables.
    """
    global _client
    _client = None
