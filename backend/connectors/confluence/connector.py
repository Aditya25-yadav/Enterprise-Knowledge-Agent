"""
Confluence Connector Orchestrator.

Implements BaseConnector to provide a unified interface for:
- Testing Confluence credentials and reachability
- Space auto-discovery across the site
- Single page loading by content ID
- Full page + Storage-Format body extraction into typed Document objects
- Incremental synchronization based on version timestamps
"""

from typing import Any, Dict, List, Optional

from backend.connectors.base import BaseConnector
from backend.connectors.confluence.client import ConfluenceClient
from backend.connectors.confluence.parser import (
    normalize_page_document,
    normalize_space_document,
)
from backend.models.document import BlockType, ContentBlock, Document, DocumentMetadata


def dict_to_content_block(data: Dict[str, Any], parent_id: Optional[str] = None) -> ContentBlock:
    """
    Recursively converts a normalized block dictionary into a typed ContentBlock object,
    preserving code language, heading levels, and nested children.
    """
    block_type = BlockType.from_string(data.get("type", "unknown"))

    properties: Dict[str, Any] = {}
    if "language" in data:
        properties["language"] = data["language"]
    if "level" in data:
        properties["level"] = data["level"]
    if "total_rows" in data:
        properties["total_rows"] = data["total_rows"]

    raw_children = data.get("children", [])
    child_blocks = [dict_to_content_block(c, parent_id=data.get("block_id")) for c in raw_children]

    columns = data.get("columns", []) or data.get("headers", [])
    rows = data.get("rows", [])

    return ContentBlock(
        id=data.get("block_id", ""),
        type=block_type,
        text=data.get("text", ""),
        properties=properties,
        parent_id=parent_id,
        children=child_blocks,
        columns=columns,
        rows=rows,
    )


def dict_to_document(doc_dict: Dict[str, Any]) -> Document:
    """
    Converts a normalized dictionary (from parser functions) into a typed Document object.
    """
    metadata = DocumentMetadata(
        id=doc_dict.get("source_id", ""),
        title=doc_dict.get("title", "Untitled"),
        source_platform=doc_dict.get("source", "confluence"),
        url=doc_dict.get("url"),
        created_time=doc_dict.get("created_at"),
        last_edited_time=doc_dict.get("updated_at"),
        created_by=doc_dict.get("created_by"),
        last_edited_by=doc_dict.get("last_edited_by"),
        parent_type=doc_dict.get("parent_type"),
        parent_id=str(doc_dict.get("parent_id")) if doc_dict.get("parent_id") is not None else None,
        extra=doc_dict.get("extra", {}),
    )

    blocks: List[ContentBlock] = []
    for b_data in doc_dict.get("content", []):
        blocks.append(dict_to_content_block(b_data))

    return Document(metadata=metadata, blocks=blocks)


class ConfluenceConnector(BaseConnector):
    """
    Enterprise Connector for Confluence Wikis, Spaces, and Pages.
    """

    def __init__(
        self,
        url: Optional[str] = None,
        username: Optional[str] = None,
        api_token: Optional[str] = None,
        space_keys: Optional[List[str]] = None,
        include_space_documents: bool = False,
    ):
        """
        Initialize the Confluence Connector.

        Args:
            url: Confluence base URL (defaults to CONFLUENCE_URL in .env).
            username: Account e-mail address (defaults to CONFLUENCE_USERNAME in .env).
            api_token: Atlassian API token (defaults to CONFLUENCE_API_TOKEN in .env).
            space_keys: Optional list of space keys to load. If empty, auto-discovers all spaces.
            include_space_documents: If True, additionally produces one Document per Space.
        """
        super().__init__(name="confluence")
        self.space_keys = space_keys or []
        self.include_space_documents = include_space_documents
        self.client = ConfluenceClient(url=url, username=username, api_token=api_token)

    def test_connection(self) -> bool:
        """Validates Confluence credentials and API reachability."""
        return self.client.test_connection()

    def get_current_user(self) -> Optional[Dict[str, Any]]:
        """Returns the authenticated Confluence account metadata."""
        return self.client.get_current_user()

    def list_spaces(self) -> List[Dict[str, Any]]:
        """Lists all spaces visible to the authenticated account."""
        return self.client.list_spaces()

    def load_document_by_id(self, doc_id: str) -> Optional[Document]:
        """
        Fetches, extracts, and normalizes a single Confluence page.

        Args:
            doc_id: Confluence numeric content ID.

        Returns:
            Typed Document object or None if failed.
        """
        try:
            page = self.client.get_page(doc_id)
            if not page:
                return None
            return dict_to_document(normalize_page_document(page))
        except Exception as e:
            print(f"⚠️ Error loading Confluence page {doc_id}: {e}")
            return None

    def load_space_document(self, space_key: str) -> Optional[Document]:
        """
        Produces a single Space-level Document (overview + metadata table).

        Args:
            space_key: Confluence space key.

        Returns:
            Typed Document object or None if the space is inaccessible.
        """
        try:
            for space in self.client.list_spaces():
                if space.get("key") == space_key:
                    return dict_to_document(normalize_space_document(space))
            print(f"⚠️ Confluence space '{space_key}' not found or inaccessible.")
            return None
        except Exception as e:
            print(f"⚠️ Error loading Confluence space {space_key}: {e}")
            return None

    def load_pages_by_space(self, space_key: str) -> List[Document]:
        """Loads all pages inside a single Confluence space."""
        documents: List[Document] = []
        pages = self.client.list_pages(space_key)
        for page in pages:
            page_id = page.get("id")
            if page_id:
                doc = self.load_document_by_id(str(page_id))
                if doc:
                    documents.append(doc)
        return documents

    def load_documents(self) -> List[Document]:
        """
        Loads accessible documents from Confluence.
        - Auto-discovers spaces (or uses space_keys filter).
        - Optionally produces Space-level documents.
        - Loads every page within each space.

        Returns:
            List of typed Document objects ready for OKF conversion and chunking.
        """
        documents: List[Document] = []

        spaces = self.client.list_spaces()
        if not spaces:
            print("⚠️ No Confluence spaces found or accessible.")
            return documents

        spaces = [s for s in spaces if not self.space_keys or s.get("key") in self.space_keys]
        print(f"📚 Found {len(spaces)} Confluence space(s).")

        for space in spaces:
            key = space.get("key") or ""
            space_docs: List[Document] = []

            if self.include_space_documents:
                space_doc = dict_to_document(normalize_space_document(space))
                if space_doc:
                    space_docs.append(space_doc)

            space_pages = self.load_pages_by_space(key)
            space_docs.extend(space_pages)
            documents.extend(space_docs)

            print(f"  → Space '{key or space.get('name')}': {len(space_pages)} page(s) "
                  f"{f'+ 1 space overview' if self.include_space_documents else ''}.")

        return documents

    def sync_incremental(self, last_sync_time: Optional[str] = None) -> List[Document]:
        """
        Fetches only pages modified after `last_sync_time` using version timestamps.

        Args:
            last_sync_time: ISO 8601 timestamp representing the previous sync time.

        Returns:
            List of newly modified or created Document objects.
        """
        if not last_sync_time:
            return self.load_documents()

        documents: List[Document] = []
        spaces = self.client.list_spaces()
        for space in spaces:
            key = space.get("key") or ""
            if self.space_keys and key not in self.space_keys:
                continue
            pages = self.client.list_pages(key)
            for page in pages:
                version = page.get("version") or {}
                edited = version.get("when")
                if edited and edited > last_sync_time:
                    page_id = page.get("id")
                    if page_id:
                        doc = self.load_document_by_id(str(page_id))
                        if doc:
                            documents.append(doc)
        return documents