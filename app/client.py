import asyncio
import hashlib
import hmac
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class UrjaPortalClientError(Exception):
    """Base exception for Urja portal communication errors."""
    pass


class UrjaAuthenticationError(UrjaPortalClientError):
    """Raised when authentication with Urja portal fails."""
    pass


class UrjaNotFoundError(UrjaPortalClientError):
    """Raised when a requested resource is not found in the portal."""
    pass


class UrjaPortalClient:
    """
    Robust HTTP client for interacting with the legacy Urja Meter Ops portal.
    Handles session lifecycle, SvelteKit CSRF headers, automatic token renewal,
    dynamic HMAC request signing, and resilient retries.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        verify_ssl: Optional[bool] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
    ):
        self.base_url = (base_url or settings.urja_portal_url).rstrip("/")
        self.username = username or settings.urja_username
        self.password = password or settings.urja_password
        self.verify_ssl = settings.verify_ssl if verify_ssl is None else verify_ssl
        self.timeout = timeout or settings.request_timeout_seconds
        self.max_retries = max_retries or settings.max_retries

        self._client: Optional[httpx.AsyncClient] = None
        self._client_loop: Optional[asyncio.AbstractEventLoop] = None
        self._auth_lock = asyncio.Lock()
        self._session_token: Optional[str] = None
        self._is_authenticated = False
        self._signing_secret: Optional[str] = None
        self._secret_fetched_at: float = 0.0

    async def _get_client(self) -> httpx.AsyncClient:
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if self._client is not None:
            if self._client.is_closed or self._client_loop != current_loop:
                self._client = None

        if self._client is None:
            cookies = {}
            if self._session_token:
                cookies["__Secure-better-auth.session_token"] = self._session_token

            self._client = httpx.AsyncClient(
                verify=self.verify_ssl,
                timeout=httpx.Timeout(self.timeout),
                cookies=cookies,
                headers={
                    "User-Agent": "Urja-API-Gateway/1.0 (+https://github.com/flock-energy)",
                    "Origin": self.base_url,
                    "Referer": f"{self.base_url}/",
                },
                follow_redirects=False,
            )
            self._client_loop = current_loop
            # Recreate auth lock for the current loop if needed
            self._auth_lock = asyncio.Lock()

        return self._client

    async def close(self):
        """Close the underlying HTTP client session."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
            self._is_authenticated = False

    async def login(self) -> bool:
        """
        Authenticate with the Urja Meter Ops portal.
        Urja ops runs SvelteKit with better-auth session cookies.
        Requires Origin & Referer headers to satisfy SvelteKit CSRF checks.
        """
        client = await self._get_client()
        login_url = f"{self.base_url}/login"
        payload = {
            "email": self.username,
            "password": self.password,
        }
        headers = {
            "Origin": self.base_url,
            "Referer": login_url,
        }

        try:
            response = await client.post(login_url, data=payload, headers=headers)
        except Exception as e:
            logger.error(f"Network error during login: {e}")
            raise UrjaAuthenticationError(f"Failed to connect to Urja login: {e}") from e

        if response.status_code == 403:
            raise UrjaAuthenticationError("Login forbidden: CSRF origin check failed or IP blocked.")

        if response.status_code in (200, 302, 303):
            # Check if cookie was received
            token = client.cookies.get("__Secure-better-auth.session_token")
            if token:
                self._session_token = token
                self._is_authenticated = True
                logger.info("Successfully authenticated with Urja portal.")
                return True

        # Check error message in response body
        try:
            body = response.json()
            error_msg = body.get("error", {}).get("message") or body.get("message")
        except Exception:
            error_msg = response.text[:200]

        raise UrjaAuthenticationError(f"Authentication failed (HTTP {response.status_code}): {error_msg}")

    async def _ensure_authenticated(self):
        """Ensure client has a valid session token, logging in if needed."""
        if not self._is_authenticated:
            async with self._auth_lock:
                if not self._is_authenticated:
                    await self.login()

    async def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        json_body: Optional[Any] = None,
        data: Optional[Any] = None,
        retry_on_auth: bool = True,
    ) -> httpx.Response:
        """
        Execute an authenticated request with automatic retry on auth failure
        and exponential backoff for transient errors.
        """
        await self._ensure_authenticated()
        client = await self._get_client()
        url = f"{self.base_url}{path}" if path.startswith("/") else f"{self.base_url}/{path}"

        request_headers = {}
        if headers:
            request_headers.update(headers)

        last_exception = None
        for attempt in range(self.max_retries):
            try:
                response = await client.request(
                    method=method,
                    url=url,
                    params=params,
                    headers=request_headers,
                    json=json_body,
                    data=data,
                )

                # Check if session expired (401, 403, or redirected to /login)
                if response.status_code in (401, 403) or (
                    response.status_code in (302, 303) and "/login" in response.headers.get("location", "")
                ):
                    if retry_on_auth:
                        logger.warning("Session token appears expired. Re-authenticating...")
                        async with self._auth_lock:
                            self._is_authenticated = False
                            await self.login()
                        return await self._request(
                            method=method,
                            path=path,
                            params=params,
                            headers=headers,
                            json_body=json_body,
                            data=data,
                            retry_on_auth=False,
                        )
                    raise UrjaAuthenticationError("Urja session expired or unauthorized.")

                if response.status_code == 404:
                    raise UrjaNotFoundError(f"Resource not found at {path}")

                if response.status_code >= 500:
                    logger.warning(f"Server error {response.status_code} at {path}, attempt {attempt + 1}")
                    await asyncio.sleep(0.5 * (2 ** attempt))
                    continue

                return response

            except (httpx.TimeoutException, httpx.NetworkError) as e:
                last_exception = e
                logger.warning(f"Network error on {method} {path} (attempt {attempt + 1}): {e}")
                await asyncio.sleep(0.5 * (2 ** attempt))

        raise UrjaPortalClientError(f"Request failed after {self.max_retries} attempts: {last_exception}")

    async def get_signing_secret(self, force_refresh: bool = False) -> str:
        """
        Fetch the HMAC signing secret from /portal/keys.
        Cached for 10 minutes to minimize extra round-trips.
        """
        now = time.time()
        if not force_refresh and self._signing_secret and (now - self._secret_fetched_at < 600):
            return self._signing_secret

        response = await self._request("GET", "/portal/keys")
        try:
            data = response.json()
            secret = data["data"]["signingSecret"]
            self._signing_secret = secret
            self._secret_fetched_at = now
            return secret
        except Exception as e:
            raise UrjaPortalClientError(f"Failed to parse signing secret from /portal/keys: {e}") from e

    async def get_bulk_export(self) -> Dict[str, Any]:
        """
        Fetch full dataset via the signed bulk export endpoint (/portal/export).
        Generates HMAC-SHA256 signature using the secret from /portal/keys.
        Returns all meters with complete hierarchy and geographic coordinates.
        """
        secret = await self.get_signing_secret()
        method = "GET"
        path = "/portal/export"
        query = "page=1"
        timestamp = str(int(time.time()))

        # Canonical signing string: "{method}\n{path}\n{query}\n{timestamp}"
        signature_payload = f"{method}\n{path}\n{query}\n{timestamp}".encode("utf-8")
        signature = hmac.new(
            secret.encode("utf-8"),
            signature_payload,
            hashlib.sha256
        ).hexdigest()

        headers = {
            "x-timestamp": timestamp,
            "x-signature": signature,
        }

        response = await self._request("GET", f"{path}?{query}", headers=headers)
        if response.status_code != 200:
            raise UrjaPortalClientError(f"Export failed with status {response.status_code}: {response.text[:200]}")

        return response.json()

    async def get_dts(self, page: int = 1) -> Dict[str, Any]:
        """Fetch distribution transformers (DTs) from /portal/dts?page={page}."""
        response = await self._request("GET", f"/portal/dts?page={page}")
        return response.json()

    async def get_all_dts(self) -> List[Dict[str, Any]]:
        """Paginate and fetch all distribution transformers across all pages."""
        all_dts: List[Dict[str, Any]] = []
        page = 1
        while True:
            res = await self.get_dts(page=page)
            data = res.get("data", [])
            total = res.get("total", 0)
            all_dts.extend(data)
            if not data or len(all_dts) >= total or page >= 10:
                break
            page += 1
        return all_dts

    async def search_portal_meters(self, query: str = "", page: int = 1) -> Dict[str, Any]:
        """
        Search meters using the portal's built-in search endpoint:
        /portal/meters/search?q={query}&page={page}
        Note: The portal only matches on meterId and serialNo.
        """
        params = {"q": query, "page": page}
        response = await self._request("GET", "/portal/meters/search", params=params)
        return response.json()

    async def get_meter_geo(self, meter_id: str) -> Optional[Dict[str, float]]:
        """
        Fetch geographic coordinates for a meter from /portal/meters/{meter_id}/geo.
        Returns dict with float latitude and longitude.
        """
        try:
            response = await self._request("GET", f"/portal/meters/{meter_id}/geo")
            data = response.json().get("data", {})
            lat = float(data.get("latitude", 0))
            lng = float(data.get("longitude", 0))
            return {"latitude": lat, "longitude": lng}
        except UrjaNotFoundError:
            return None
        except Exception as e:
            logger.warning(f"Error fetching geo for meter {meter_id}: {e}")
            return None

    async def get_meter_energy(self, meter_id: str) -> List[Dict[str, Any]]:
        """
        Fetch 30-minute interval energy consumption readings for a meter from
        /portal/meters/{meter_id}/energy.
        """
        response = await self._request("GET", f"/portal/meters/{meter_id}/energy")
        return response.json().get("data", [])

    async def get_meter_page_details(self, meter_id: str) -> Dict[str, Any]:
        """
        Extract full meter detail and hierarchy from the portal's SSR page:
        /meters/{meter_id}.
        Handles the two distinct historical data formats:
        - 'legacy': detail.data -> array of {parameterName, parameterValue}
        - 'v2': detail.classData -> JSON string with {installed_meter: {...}}
        """
        response = await self._request("GET", f"/meters/{meter_id}")
        html = response.text

        # Extract SvelteKit hydration payload inside <script>
        script_pattern = re.search(r"kit\.start\(app,\s*element,\s*(\{.*?\})\s*\);", html, re.DOTALL)
        if script_pattern:
            kit_arg = script_pattern.group(1)
            # Find data payload for node 6 (meter detail node)
            node_match = re.search(
                r'\{type:"data",data:(\{meterId:"' + re.escape(meter_id) + r'".*?\}),uses:',
                kit_arg,
                re.DOTALL
            )
            if node_match:
                raw_json = node_match.group(1)
                try:
                    # Parse standard JSON properties
                    parsed = self._parse_relaxed_json(raw_json)
                    return parsed
                except Exception as e:
                    logger.debug(f"Failed relaxed JSON parse for meter {meter_id}: {e}")

        # Fallback: extract detail parameters and hierarchy breadcrumbs from HTML
        return self._extract_meter_from_html(html, meter_id)

    def _parse_relaxed_json(self, s: str) -> Dict[str, Any]:
        """Parse relaxed JS object literal into Python dictionary."""
        # Convert unquoted keys: {meterId: "..."} -> {"meterId": "..."}
        fixed = re.sub(r'([{,])\s*([a-zA-Z_][a-zA-Z0-9_\s]*?)\s*:', r'\1"\2":', s)
        return json.loads(fixed)

    def _extract_meter_from_html(self, html: str, meter_id: str) -> Dict[str, Any]:
        """Fallback DOM extractor for meter detail page."""
        details: Dict[str, Any] = {"meterId": meter_id, "detail": {"data": []}, "hierarchy": {}}
        # Search for dl items (Nameplate)
        dt_dd_pattern = re.findall(r'<dt[^>]*>\s*(.*?)\s*</dt>\s*<dd[^>]*>\s*(.*?)\s*</dd>', html)
        for name, value in dt_dd_pattern:
            details["detail"]["data"].append({"parameterName": name.strip(), "parameterValue": value.strip()})

        # Search for hierarchy breadcrumbs
        nav_pattern = re.search(r'<nav[^>]*class="[^"]*text-xs[^"]*"[^>]*>(.*?)</nav>', html, re.DOTALL)
        if nav_pattern:
            crumbs = re.findall(r'<span[^>]*>(.*?)</span>', nav_pattern.group(1))
            levels = ["Zone", "Circle", "Division", "Subdivision", "Sub Station", "Feeder", "DT"]
            for i, crumb in enumerate(crumbs):
                if i < len(levels):
                    details["hierarchy"][levels[i]] = crumb.strip()

        return details
