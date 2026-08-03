# Setup

## 1. Install dependencies
```bash
pip install -r requirements.txt
```

## 2. Google Drive API access
1. Go to [console.cloud.google.com](https://console.cloud.google.com) → create or select a project.
2. Enable the **Google Drive API** for that project (APIs & Services → Library → search "Google Drive API" → Enable).
3. Configure the **OAuth consent screen** (User type: External is fine for personal testing; add yourself as a test user).
4. Go to **Credentials → Create Credentials → OAuth client ID → Application type: Desktop app**.
5. Download the resulting JSON and save it as `credentials.json` in the project root.

## 3. Groq API key (free, for answer generation)
1. Go to [console.groq.com](https://console.groq.com) → sign up → create an API key.
2. Copy it into your `.env` file as `GROQ_API_KEY`.

## 4. Environment file
```bash
cp .env.example .env
# then edit .env and paste in your GROQ_API_KEY
```

## 5. First run
```bash
python main.py ingest
```
This opens a browser window on first run — log in and grant read-only Drive access. It then lists every file it can extract text from, chunks it, embeds it locally (first run also downloads the ~130MB embedding model), and writes everything into a local Chroma DB at `./chroma_db`.

To scope ingestion to a single folder instead of your whole Drive:
```bash
python main.py ingest --folder-id <folder_id_from_the_drive_url>
```

## 6. Ask questions
```bash
python main.py query "What does the CLM implementation timeline look like?"
```

## What's NOT implemented yet (by design — deferred)
- OKF normalization layer (`okf/schema.py` exists as a scaffold but isn't wired in)
- Permission-aware filtering at query time (permissions are fetched and stored in the raw chunk dict, but not yet enforced during retrieval)
- Keyword search and knowledge graph / Graph RAG
- Agent planner (currently always does plain vector search)

This is a straight vector-RAG loop end to end: Drive → extract → chunk → embed → store → retrieve → generate cited answer. Once this is solid, permission filtering is the next thing to layer in — it slots into `rag/vector_store.py`'s `query()` function.
