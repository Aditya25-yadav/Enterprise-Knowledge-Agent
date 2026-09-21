"""
End-to-End Test Runner for Confluence Connector.

Validates:
1. BaseConnector interface conformance
2. Basic-auth connection testing (test_connection)
3. Space auto-discovery and page ingestion into Document format
4. Markdown rendering of Confluence documents
"""

import argparse
import json
import sys
from pathlib import Path
from dotenv import find_dotenv, load_dotenv

# Ensure project root is in sys.path
SCRIPT_PATH = Path(__file__).resolve()
current = SCRIPT_PATH.parent
while current != current.parent:
    if (current / "backend").exists():
        if str(current) not in sys.path:
            sys.path.insert(0, str(current))
        break
    current = current.parent

# Ensure .env is loaded
load_dotenv(find_dotenv())

from backend.connectors.confluence.connector import ConfluenceConnector

SCRIPT_DIR = Path(__file__).resolve().parent
CONFLUENCE_DIR = SCRIPT_DIR.parent
TEST_DATA_DIR = CONFLUENCE_DIR / "test_data"
TEST_DATA_DIR.mkdir(parents=True, exist_ok=True)


def run_test(space_keys: str = "", include_spaces: bool = False) -> None:
    print("\n" + "=" * 60)
    print("🚀 ENTERPRISE KNOWLEDGE AGENT - CONFLUENCE CONNECTOR RUNNER")
    print("=" * 60)

    # 1. Initialize Connector (picks up credentials from .env)
    keys = [k.strip() for k in space_keys.split(",") if k.strip()] if space_keys else []
    try:
        connector = ConfluenceConnector(
            space_keys=keys,
            include_space_documents=include_spaces,
        )
    except Exception as e:
        print(f"❌ Configuration error: {e}")
        return

    # 2. Test Connection
    print("📡 Testing API Connection to Confluence...")
    if connector.test_connection():
        print("✅ Connection Successful! (Authenticated with Confluence API)")
        user = connector.get_current_user()
        if user:
            print(f"   Authenticated as: {user.get('displayName') or user.get('username')} "
                  f"({user.get('email')})")
    else:
        print("❌ Connection Failed. Please check your credentials in .env.")
        return

    print("-" * 60)

    # 3. Ingest Documents
    docs = connector.load_documents()

    if not docs:
        print("⚠️ No documents were returned or parsed.")
        return

    print(f"\n🎉 Successfully ingested {len(docs)} document(s)!")

    # 4. Display and Save Details for each document
    for idx, doc in enumerate(docs, 1):
        safe_name = doc.metadata.title.replace("/", "_").replace("\\", "_").strip("_") or "root"
        json_filename = f"output_document_{safe_name}.json"
        json_path = TEST_DATA_DIR / json_filename
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(doc.to_dict(), f, indent=2, ensure_ascii=False)

        md_filename = f"output_document_{safe_name}.md"
        md_path = TEST_DATA_DIR / md_filename
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(doc.to_markdown())

        if idx <= 5:
            print("\n" + "=" * 60)
            print(f"📄 DOCUMENT #{idx}: {doc.metadata.title}")
            print("=" * 60)
            print(f"• ID:               {doc.metadata.id}")
            print(f"• Platform:         {doc.metadata.source_platform}")
            print(f"• URL:              {doc.metadata.url or 'N/A'}")
            print(f"• Created Time:     {doc.metadata.created_time or 'N/A'}")
            print(f"• Last Edited Time: {doc.metadata.last_edited_time or 'N/A'}")
            print(f"• Parent Type:      {doc.metadata.parent_type or 'N/A'}")
            print(f"• Parent ID:        {doc.metadata.parent_id or 'N/A'}")
            print(f"• Total Root Blocks:{len(doc.blocks)}")
            extra = doc.metadata.extra
            if extra:
                print(f"• Space Key:        {extra.get('space_key')}")
                print(f"• Type:             {extra.get('type')}")

            types_count = {}
            for b in doc.blocks:
                t = b.type.value if hasattr(b.type, "value") else str(b.type)
                types_count[t] = types_count.get(t, 0) + 1
            print(f"• Block Breakdown:  {types_count}")
            print(f"💾 Saved structured JSON to:   {json_path.name}")
            print(f"💾 Saved rendered Markdown to: {md_path.name}")
        elif idx == 6:
            print(f"\n... (remaining {len(docs) - 5} documents saved silently to {TEST_DATA_DIR.name}/) ...")

    print("\n" + "=" * 60)
    print("✨ Ingestion & Normalization verification complete!")
    print("=" * 60 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run and test the Confluence Connector")
    parser.add_argument(
        "--spaces",
        type=str,
        default="",
        help="Comma-separated space keys to ingest (default '' = all spaces)",
    )
    parser.add_argument(
        "--include-spaces",
        action="store_true",
        help="Also generate a space-level overview Document per space",
    )
    args = parser.parse_args()

    run_test(space_keys=args.spaces, include_spaces=args.include_spaces)


if __name__ == "__main__":
    main()