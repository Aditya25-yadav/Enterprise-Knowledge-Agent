"""
Google Drive connector.

Responsible for:
  1. Listing files (optionally scoped to a folder)
  2. Extracting plain text from each supported file type
  3. Fetching permission metadata for each file
  4. Chunking the extracted text

Output of ingest() is a flat list of plain dicts — no OKF wrapping yet,
that gets layered back in once the RAG loop is proven out. Each dict has
everything the embedding + vector store step needs, plus enough metadata
(url, title, permissions) to support citations and access filtering later.
"""

from __future__ import annotations
import io
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from pypdf import PdfReader

from connectors.google_drive.auth import get_credentials
from rag.chunking import chunk_text

# Google-native mimetypes that must be *exported* (converted) rather than downloaded raw
EXPORTABLE_MIME_MAP = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.presentation": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
}

# Non-Google file types we know how to extract text from directly
DIRECT_TEXT_MIME_TYPES = {"text/plain", "text/markdown", "text/csv"}

SUPPORTED_MIME_TYPES = set(EXPORTABLE_MIME_MAP) | DIRECT_TEXT_MIME_TYPES | {"application/pdf"}


def get_drive_service():
    creds = get_credentials()
    return build("drive", "v3", credentials=creds)


def list_files(service, folder_id: str | None = None, page_size: int = 100) -> list[dict]:
    """
    Lists files, optionally scoped to a folder. Only returns mimetypes this
    connector knows how to extract text from — everything else is skipped
    (logged, not silently dropped).
    """
    query_parts = ["trashed = false"]
    if folder_id:
        query_parts.append(f"'{folder_id}' in parents")
    query = " and ".join(query_parts)

    fields = (
        "nextPageToken, files(id, name, mimeType, webViewLink, "
        "owners(displayName, emailAddress), createdTime, modifiedTime)"
    )

    files: list[dict] = []
    skipped = 0
    page_token = None

    while True:
        response = service.files().list(
            q=query,
            spaces="drive",
            fields=fields,
            pageSize=page_size,
            pageToken=page_token,
        ).execute()

        for f in response.get("files", []):
            if f["mimeType"] in SUPPORTED_MIME_TYPES:
                files.append(f)
            else:
                skipped += 1

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    if skipped:
        print(f"[google_drive] skipped {skipped} file(s) with unsupported mime types")

    return files


def get_permissions(service, file_id: str) -> list[dict]:
    """
    Fetches the permission list for a file. Returns raw Drive permission
    records (type, role, emailAddress/domain) — normalization into a
    unified permission format happens later, once OKF is layered back in.
    """
    try:
        response = service.permissions().list(
            fileId=file_id,
            fields="permissions(id, type, role, emailAddress, domain)",
        ).execute()
        return response.get("permissions", [])
    except Exception as e:
        print(f"[google_drive] could not fetch permissions for {file_id}: {e}")
        return []


def extract_text(service, file_id: str, mime_type: str) -> str:
    """Dispatches to export or direct download depending on mimetype, then decodes to text."""
    if mime_type in EXPORTABLE_MIME_MAP:
        export_mime = EXPORTABLE_MIME_MAP[mime_type]
        request = service.files().export_media(fileId=file_id, mimeType=export_mime)
        raw = _download(request)
        return raw.decode("utf-8", errors="ignore")

    if mime_type == "application/pdf":
        request = service.files().get_media(fileId=file_id)
        raw = _download(request)
        return _extract_pdf_text(raw)

    if mime_type in DIRECT_TEXT_MIME_TYPES:
        request = service.files().get_media(fileId=file_id)
        raw = _download(request)
        return raw.decode("utf-8", errors="ignore")

    raise ValueError(f"Unsupported mime type for extraction: {mime_type}")


def _download(request) -> bytes:
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buffer.getvalue()


def _extract_pdf_text(raw: bytes) -> str:
    reader = PdfReader(io.BytesIO(raw))
    pages = []
    for page in reader.pages:
        pages.append(page.extract_text() or "")
    return "\n\n".join(pages)


def ingest(
    folder_id: str | None = None,
    chunk_size: int = 1000,
    overlap: int = 150,
) -> list[dict]:
    """
    Full ingest pass: list files -> extract text -> fetch permissions -> chunk.
    Returns a flat list of chunk dicts ready to hand to the embedder.
    """
    service = get_drive_service()
    files = list_files(service, folder_id=folder_id)
    print(f"[google_drive] found {len(files)} file(s) to process")

    all_chunks: list[dict] = []

    for f in files:
        file_id = f["id"]
        title = f["name"]
        mime_type = f["mimeType"]

        try:
            text = extract_text(service, file_id, mime_type)
        except Exception as e:
            print(f"[google_drive] failed to extract '{title}' ({file_id}): {e}")
            continue

        if not text.strip():
            print(f"[google_drive] skipping '{title}' — no extractable text")
            continue

        permissions = get_permissions(service, file_id)
        owner = next(
            (o.get("emailAddress") for o in f.get("owners", []) if o.get("emailAddress")),
            None,
        )

        pieces = chunk_text(text, chunk_size=chunk_size, overlap=overlap)

        for idx, piece in enumerate(pieces):
            all_chunks.append({
                "chunk_id": f"{file_id}_{idx}",
                "document_id": file_id,
                "source_system": "google_drive",
                "title": title,
                "text": piece,
                "chunk_index": idx,
                "url": f.get("webViewLink"),
                "author": owner,
                "created_at": f.get("createdTime"),
                "updated_at": f.get("modifiedTime"),
                "mime_type": mime_type,
                "permissions": permissions,
            })

        print(f"[google_drive] processed '{title}' -> {len(pieces)} chunk(s)")

    print(f"[google_drive] total chunks produced: {len(all_chunks)}")
    return all_chunks


if __name__ == "__main__":
    import json
    chunks = ingest()
    with open("drive_chunks.json", "w") as f:
        json.dump(chunks, f, indent=2, default=str)
    print(f"Wrote {len(chunks)} chunks to drive_chunks.json")
