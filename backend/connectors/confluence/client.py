"""
Confluence API Client.

Handles all network I/O with the Confluence Cloud REST API (v1), including:
- Credential validation and connection testing (GET /rest/api/user/current)
- Space auto-discovery (GET /rest/api/space)
- Page listing per space (GET /rest/api/content) with cursor-style pagination
- Full page retrieval including Storage Format body (expand=body.storage)
- Resilient error recovery so one inaccessible space never halts ingestion
"""

import base64
import os
from typing import Any, Dict, List, Optional

import dotenv
import requests

dotenv.load_dotenv()


class ConfluenceClient:
    """
    Client for interacting with the Confluence REST API (v1).
    Uses HTTP Basic authentication (email + API token) for Atlassian Cloud.
    """

    REST_PATH = "/rest/api"

    def __init__(
        self,
        url: Optional[str] = None,
        username: Optional[str] = None,
        api_token: Optional[str] = None,
    ):
        """
        Initialize the Confluence client.

        Args:
            url: Confluence base URL (defaults to CONFLUENCE_URL in .env).
            username: Account e-mail address (defaults to CONFLUENCE_USERNAME in .env).
            api_token: Atlassian API token (defaults to CONFLUENCE_API_TOKEN in .env).
        """
        self.username = username or os.getenv("CONFLUENCE_USERNAME")
        self.api_token = api_token or os.getenv("CONFLUENCE_API_TOKEN")
        self.base_url = (url or os.getenv("CONFLUENCE_URL") or "").rstrip("/")

        # Cloud instances expose the REST API under /wiki; Server/DC can use a bare
        # domain or an explicit /rest/api suffix. Accept whichever is provided.
        if self.base_url and "/wiki" not in self.base_url and not self.base_url.endswith(self.REST_PATH):
            self.base_url += "/wiki"

        if not (self.base_url and self.username and self.api_token):
            raise ValueError(
                "Confluence credentials missing: Provide CONFLUENCE_URL, "
                "CONFLUENCE_USERNAME, and CONFLUENCE_API_TOKEN in .env."
            )

        basic = base64.b64encode(f"{self.username}:{self.api_token}".encode("utf-8")).decode("ascii")
        self.headers = {
            "Authorization": f"Basic {basic}",
            "Accept": "application/json",
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)

    def test_connection(self) -> bool:
        """
        Validates credentials and API reachability via GET /rest/api/user/current.

        Returns:
            True if connection and authentication succeed, False otherwise.
        """
        try:
            url = f"{self.base_url}{self.REST_PATH}/user/current"
            response = self.session.get(url)
            return response.status_code == 200
        except Exception as e:
            print(f"⚠️ Confluence connection test failed: {e}")
            return False

    def get_current_user(self) -> Optional[Dict[str, Any]]:
        """
        Returns the metadata of the authenticated user.
        """
        try:
            url = f"{self.base_url}{self.REST_PATH}/user/current"
            response = self.session.get(url)
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            print(f"⚠️ Could not fetch Confluence user info: {e}")
        return None

    def list_spaces(self, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Lists all spaces visible to the authenticated account.

        Args:
            limit: Max spaces requested per page.

        Returns:
            List of space metadata dictionaries.
        """
        spaces: List[Dict[str, Any]] = []
        url = f"{self.base_url}{self.REST_PATH}/space"
        params: Dict[str, Any] = {"limit": limit, "expand": "description.plain,homepage"}

        while url:
            response = self.session.get(url, params=params)
            if response.status_code != 200:
                break
            params = {}
            data = response.json()
            spaces.extend(data.get("results", []))
            url = self._next_url(data, response)
            if not data.get("results"):
                break

        return spaces

    def list_pages(
        self,
        space_key: str,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        Lists content (pages/blogposts) inside a space.

        Args:
            space_key: Confluence space key (e.g. 'ENG', 'PROJ').
            limit: Max content items requested per page.

        Returns:
            List of content metadata dictionaries (no body included).
        """
        pages: List[Dict[str, Any]] = []
        url = f"{self.base_url}{self.REST_PATH}/content"
        params: Dict[str, Any] = {
            "spaceKey": space_key,
            "limit": limit,
            "expand": "version,space,history,ancestors",
        }

        while url:
            response = self.session.get(url, params=params)
            if response.status_code != 200:
                print(f"⚠️ Could not list pages for space '{space_key}' (HTTP {response.status_code}).")
                break
            params = {}
            data = response.json()
            pages.extend(data.get("results", []))
            url = self._next_url(data, response)
            if not data.get("results"):
                break

        return pages

    def get_page(self, page_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetches a single Confluence page including its Storage Format body.

        Args:
            page_id: Confluence numeric content ID.

        Returns:
            Full page metadata dictionary, or None if not found / inaccessible.
        """
        try:
            url = f"{self.base_url}{self.REST_PATH}/content/{page_id}"
            params = {"expand": "body.storage,version,space,history,ancestors"}
            response = self.session.get(url, params=params)
            if response.status_code != 200:
                print(f"⚠️ Confluence page {page_id} not found or inaccessible (HTTP {response.status_code}).")
                return None
            return response.json()
        except Exception as e:
            print(f"⚠️ Could not fetch Confluence page {page_id}: {e}")
            return None

    @staticmethod
    def _next_url(data: Dict[str, Any], response: requests.Response) -> Optional[str]:
        """
        Resolves the '_links.next' value returned by Confluence pagination.
        '_links.next' is relative to '_links.base'; combine them into an absolute URL.
        """
        links = data.get("_links") or {}
        next_path = links.get("next")
        if not next_path:
            return None
        base = links.get("base") or ""
        if next_path.startswith("http"):
            return next_path
        return f"{base}{next_path}"