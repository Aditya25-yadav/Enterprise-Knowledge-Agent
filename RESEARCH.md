# Where Does MCP Fit?

This is where things get really interesting.

**MCP = Model Context Protocol.**

It is fundamentally different from OKF.

A useful distinction is:

|                            | **OKF**                         | **MCP**                                          |
| -------------------------- | ------------------------------- | ------------------------------------------------ |
| **What is it?**            | Knowledge representation format | Protocol                                         |
| **Main purpose**           | Represent knowledge             | Connect AI applications to external capabilities |
| **Basic unit**             | Knowledge bundle / concept      | Server / tool / resource / prompt                |
| **Typical representation** | Markdown + YAML                 | Protocol messages / JSON                         |
| **Think of it as**         | **“How knowledge is packaged”** | **“How an agent accesses capabilities”**         |
| **Runtime required?**      | No                              | Yes                                              |
| **Search engine?**         | No                              | No                                               |
| **Database?**              | No                              | No                                               |

The current MCP specification describes MCP as the protocol layer for connecting AI applications with external systems, with capabilities such as **tools, resources, and prompts**. The July 2026 specification also introduced a stateless protocol core and strengthened authorization.

So:

> **OKF describes knowledge. MCP exposes capabilities.**

---

# What OKF Actually Is

The simplest mental model is:

> **OKF is a portable, human-readable knowledge representation format — not a database, not RAG, and not an API protocol.**

An OKF knowledge bundle is essentially:

```text
knowledge-bundle/
│
├── index.md
├── log.md
│
├── projects/
│   ├── index.md
│   ├── project-alpha.md
│   └── project-beta.md
│
├── services/
│   ├── payment-service.md
│   └── auth-service.md
│
└── teams/
    └── backend-team.md
```

Each concept is a Markdown file containing:

**YAML frontmatter + Markdown body**

For example, conceptually:

```yaml
---
type: Service
title: Payment Service
description: Handles payment processing
resource: https://...
tags: [payments, backend]
---
```

followed by Markdown describing the service and linking it to other concepts.

The important part is that relationships can be expressed through links between concepts.

So:

```text
payment-service.md
        │
        │ owned by
        ▼
backend-team.md
```

can become traversable knowledge.

---

# Think About the Notion API Model

This is the first thing I want you to understand before implementation.

A Notion page is not simply:

```text
Page
└── content: "some huge string"
```

Instead, Notion's content is structured into **blocks**.

Conceptually:

```text
Page
│
├── Page metadata
│   ├── ID
│   ├── URL
│   ├── title
│   ├── created time
│   └── last edited time
│
└── Blocks
    ├── paragraph
    ├── heading
    ├── paragraph
    ├── bulleted list
    ├── code
    ├── image
    └── ...
```

This distinction matters a lot for our eventual knowledge pipeline.

```text
Notion API JSON
        ↓
Extract page metadata
        ↓
Extract block content
        ↓
Flatten/normalize rich text
        ↓
Preserve document structure
        ↓
Store normalized JSON
```

The important point is:

> **Don't throw away information too aggressively yet.**

We want to extract the information useful for our agent while retaining enough metadata for retrieval, citations, permissions, and future graph construction.

---

# Notion Connector: Three Processing Layers

For the Notion connector, I suggest we think about processing in three layers.

## 1. Page-Level Information

From `/pages/{id}`, keep:

- `page_id`
- `title`
- `url`
- `created_time`
- `last_edited_time`
- `parent`
- `created_by`
- `last_edited_by`

The most important ones initially are:

- **`page_id`** → stable source identifier
- **`title`** → useful for retrieval/citations
- **`url`** → source citation
- **`last_edited_time`** → synchronization later
- **`parent`** → hierarchy/context

We don't need to carry the entire raw page JSON forward.

---

## 2. Block-Level Information

From `/blocks/{id}/children`, we want to transform blocks into meaningful **text + structure**.

For example:

```text
heading_1
"Architecture"

paragraph
"The backend uses FastAPI."

bulleted_list_item
"Neo4j is used for graph storage."
```

becomes something conceptually like:

```json
[
  {
    "type": "heading",
    "text": "Architecture"
  },
  {
    "type": "paragraph",
    "text": "The backend uses FastAPI."
  },
  {
    "type": "list_item",
    "text": "Neo4j is used for graph storage."
  }
]
```

We're essentially converting:

```text
Notion-specific JSON
        ↓
Useful structured knowledge
```

The reason we don't simply extract every `plain_text` value and concatenate it is that **structure carries meaning**.

For example:

```text
Architecture

FastAPI

PostgreSQL
```

is more useful than:

```text
Architecture FastAPI PostgreSQL
```

when we eventually chunk and retrieve it.

---

## 3. Preserve Source Information

Every extracted piece should still know where it came from.

Conceptually:

```json
{
  "page_id": "...",
  "page_title": "Project Architecture",
  "page_url": "...",
  "block_id": "...",
  "block_type": "paragraph",
  "text": "Backend uses FastAPI."
}
```

Why?

Later, if the agent says:

> The backend uses FastAPI.

we need to know:

- Which page?
- Which part of the page?
- What URL should I cite?

It also becomes important for updating/deleting knowledge when the Notion page changes.

---

# The Extraction Pipeline

Therefore, our extraction pipeline should be:

```text
Page API
    │
    ▼
Page metadata
    │
    ▼
Fetch blocks
    │
    ├── pagination ─────────┐
    │                       │
    ▼                       │
Block                       │
    │                       │
    ├── has_children?       │
    │       │               │
    │      yes              │
    │       ▼               │
    │   fetch children ─────┘
    │
    ▼
Extract useful content
    │
    ▼
Normalized Document
```

And this is another reason I would separate **retrieval from normalization**:

```text
retrieve_page()
retrieve_all_blocks()
        ↓
complete Notion data
        ↓
normalize()
        ↓
our Document model
```

That way, `normalize()` can assume we've already attempted to retrieve the complete page tree.

One more subtle point: we should also preserve the **hierarchy**, rather than flattening everything immediately. A heading, toggle, list, and nested block can carry contextual meaning.

We can flatten later during chunking if that's the best retrieval strategy.

---

# Do We Need a Database Model?

Yes, but **not necessarily a database model yet**.

For our project, I recommend having a small **data model for the normalized representation**. It gives us a clear contract between the Notion connector and everything downstream.

Think of three levels:

```text
Notion API JSON
      ↓
Notion-specific models
      ↓
Normalized Knowledge Document
      ↓
OKF
```

## 1. Do We Need Models for Raw Notion Responses?

**No, not initially.**

We can work with the API JSON dictionaries while learning the Notion structure.

For example:

```python
page = response.json()
blocks = response.json()
```

Creating models for every Notion API object would add unnecessary complexity right now.

---

## 2. Do We Need a Model for Our Normalized Document?

**Yes.**

Something conceptually like:

```python
class Document:
    source: str
    source_id: str
    title: str
    url: str
    created_at: ...
    updated_at: ...
    content: list[ContentBlock]
```

And:

```python
class ContentBlock:
    block_id: str
    type: str
    text: str
```

I would use **Pydantic** for these because we're building a Python/FastAPI system.

The benefit isn't just type checking. It establishes a contract:

```text
Notion Connector
       ↓
    Document
       ↓
Processing pipeline
```

The processing pipeline can trust that every `Document` has the expected structure regardless of where it came from.

Later:

```text
Notion ──┐
Slack ───┤
GitHub ──┤──→ Document
Drive ───┘
```

That's where the model becomes particularly valuable.

---

# One Important Distinction

I wouldn't make this model overly detailed yet.

Don't create:

```text
NotionPageModel
NotionParagraphModel
NotionHeadingModel
NotionCodeModel
NotionCalloutModel
...
```

unless we actually need them.

For our current stage, I'd keep it simple:

```text
Document
└── ContentBlock
```

This gives us a clean foundation without prematurely coupling the normalized knowledge model to every detail of the Notion API.

# Where Does MCP Fit?

This is where things get really interesting.

**MCP = Model Context Protocol.**

It is fundamentally different from OKF.

A useful distinction is:

|                            | **OKF**                         | **MCP**                                          |
| -------------------------- | ------------------------------- | ------------------------------------------------ |
| **What is it?**            | Knowledge representation format | Protocol                                         |
| **Main purpose**           | Represent knowledge             | Connect AI applications to external capabilities |
| **Basic unit**             | Knowledge bundle / concept      | Server / tool / resource / prompt                |
| **Typical representation** | Markdown + YAML                 | Protocol messages / JSON                         |
| **Think of it as**         | **“How knowledge is packaged”** | **“How an agent accesses capabilities”**         |
| **Runtime required?**      | No                              | Yes                                              |
| **Search engine?**         | No                              | No                                               |
| **Database?**              | No                              | No                                               |

The current MCP specification describes MCP as the protocol layer for connecting AI applications with external systems, with capabilities such as **tools, resources, and prompts**. The July 2026 specification also introduced a stateless protocol core and strengthened authorization.

So:

> **OKF describes knowledge. MCP exposes capabilities.**

---

# What OKF Actually Is

The simplest mental model is:

> **OKF is a portable, human-readable knowledge representation format — not a database, not RAG, and not an API protocol.**

An OKF knowledge bundle is essentially:

```text
knowledge-bundle/
│
├── index.md
├── log.md
│
├── projects/
│   ├── index.md
│   ├── project-alpha.md
│   └── project-beta.md
│
├── services/
│   ├── payment-service.md
│   └── auth-service.md
│
└── teams/
    └── backend-team.md
```

Each concept is a Markdown file containing:

**YAML frontmatter + Markdown body**

For example, conceptually:

```yaml
---
type: Service
title: Payment Service
description: Handles payment processing
resource: https://...
tags: [payments, backend]
---
```

followed by Markdown describing the service and linking it to other concepts.

The important part is that relationships can be expressed through links between concepts.

So:

```text
payment-service.md
        │
        │ owned by
        ▼
backend-team.md
```

can become traversable knowledge.

---

# Think About the Notion API Model

This is the first thing I want you to understand before implementation.

A Notion page is not simply:

```text
Page
└── content: "some huge string"
```

Instead, Notion's content is structured into **blocks**.

Conceptually:

```text
Page
│
├── Page metadata
│   ├── ID
│   ├── URL
│   ├── title
│   ├── created time
│   └── last edited time
│
└── Blocks
    ├── paragraph
    ├── heading
    ├── paragraph
    ├── bulleted list
    ├── code
    ├── image
    └── ...
```

This distinction matters a lot for our eventual knowledge pipeline.

```text
Notion API JSON
        ↓
Extract page metadata
        ↓
Extract block content
        ↓
Flatten/normalize rich text
        ↓
Preserve document structure
        ↓
Store normalized JSON
```

The important point is:

> **Don't throw away information too aggressively yet.**

We want to extract the information useful for our agent while retaining enough metadata for retrieval, citations, permissions, and future graph construction.

---

# Notion Connector: Three Processing Layers

For the Notion connector, I suggest we think about processing in three layers.

## 1. Page-Level Information

From `/pages/{id}`, keep:

- `page_id`
- `title`
- `url`
- `created_time`
- `last_edited_time`
- `parent`
- `created_by`
- `last_edited_by`

The most important ones initially are:

- **`page_id`** → stable source identifier
- **`title`** → useful for retrieval/citations
- **`url`** → source citation
- **`last_edited_time`** → synchronization later
- **`parent`** → hierarchy/context

We don't need to carry the entire raw page JSON forward.

---

## 2. Block-Level Information

From `/blocks/{id}/children`, we want to transform blocks into meaningful **text + structure**.

For example:

```text
heading_1
"Architecture"

paragraph
"The backend uses FastAPI."

bulleted_list_item
"Neo4j is used for graph storage."
```

becomes something conceptually like:

```json
[
  {
    "type": "heading",
    "text": "Architecture"
  },
  {
    "type": "paragraph",
    "text": "The backend uses FastAPI."
  },
  {
    "type": "list_item",
    "text": "Neo4j is used for graph storage."
  }
]
```

We're essentially converting:

```text
Notion-specific JSON
        ↓
Useful structured knowledge
```

The reason we don't simply extract every `plain_text` value and concatenate it is that **structure carries meaning**.

For example:

```text
Architecture

FastAPI

PostgreSQL
```

is more useful than:

```text
Architecture FastAPI PostgreSQL
```

when we eventually chunk and retrieve it.

---

## 3. Preserve Source Information

Every extracted piece should still know where it came from.

Conceptually:

```json
{
  "page_id": "...",
  "page_title": "Project Architecture",
  "page_url": "...",
  "block_id": "...",
  "block_type": "paragraph",
  "text": "Backend uses FastAPI."
}
```

Why?

Later, if the agent says:

> The backend uses FastAPI.

we need to know:

- Which page?
- Which part of the page?
- What URL should I cite?

It also becomes important for updating/deleting knowledge when the Notion page changes.

---

# The Extraction Pipeline

Therefore, our extraction pipeline should be:

```text
Page API
    │
    ▼
Page metadata
    │
    ▼
Fetch blocks
    │
    ├── pagination ─────────┐
    │                       │
    ▼                       │
Block                       │
    │                       │
    ├── has_children?       │
    │       │               │
    │      yes              │
    │       ▼               │
    │   fetch children ─────┘
    │
    ▼
Extract useful content
    │
    ▼
Normalized Document
```

And this is another reason I would separate **retrieval from normalization**:

```text
retrieve_page()
retrieve_all_blocks()
        ↓
complete Notion data
        ↓
normalize()
        ↓
our Document model
```

That way, `normalize()` can assume we've already attempted to retrieve the complete page tree.

One more subtle point: we should also preserve the **hierarchy**, rather than flattening everything immediately. A heading, toggle, list, and nested block can carry contextual meaning.

We can flatten later during chunking if that's the best retrieval strategy.

---

# Do We Need a Database Model?

Yes, but **not necessarily a database model yet**.

For our project, I recommend having a small **data model for the normalized representation**. It gives us a clear contract between the Notion connector and everything downstream.

Think of three levels:

```text
Notion API JSON
      ↓
Notion-specific models
      ↓
Normalized Knowledge Document
      ↓
OKF
```

## 1. Do We Need Models for Raw Notion Responses?

**No, not initially.**

We can work with the API JSON dictionaries while learning the Notion structure.

For example:

```python
page = response.json()
blocks = response.json()
```

Creating models for every Notion API object would add unnecessary complexity right now.

---

## 2. Do We Need a Model for Our Normalized Document?

**Yes.**

Something conceptually like:

```python
class Document:
    source: str
    source_id: str
    title: str
    url: str
    created_at: ...
    updated_at: ...
    content: list[ContentBlock]
```

And:

```python
class ContentBlock:
    block_id: str
    type: str
    text: str
```

I would use **Pydantic** for these because we're building a Python/FastAPI system.

The benefit isn't just type checking. It establishes a contract:

```text
Notion Connector
       ↓
    Document
       ↓
Processing pipeline
```

The processing pipeline can trust that every `Document` has the expected structure regardless of where it came from.

Later:

```text
Notion ──┐
Slack ───┤
GitHub ──┤──→ Document
Drive ───┘
```

That's where the model becomes particularly valuable.

---

# One Important Distinction

I wouldn't make this model overly detailed yet.

Don't create:

```text
NotionPageModel
NotionParagraphModel
NotionHeadingModel
NotionCodeModel
NotionCalloutModel
...
```

unless we actually need them.

For our current stage, I'd keep it simple:

```text
Document
└── ContentBlock
```

This gives us a clean foundation without prematurely coupling the normalized knowledge model to every detail of the Notion API.

---

Exactly. One page ID is only our unit of processing, not the scope of the connector.

We ultimately need to synchronize the entire set of Notion pages that our integration is allowed to access.

The architecture becomes:

```text
                    Notion Workspace
                          │
                          ▼
                  Discover accessible pages
                          │
             ┌────────────┼────────────┐
             ▼            ▼            ▼
          Page A        Page B       Page C
             │            │            │
             ▼            ▼            ▼
          Extract      Extract      Extract
             │            │            │
             └────────────┼────────────┘
                          ▼
                    Normalize
                          ▼
                    Knowledge Store
```

The important part: synchronization

We don't want to repeatedly process every page.

We need to distinguish:

NEW
MODIFIED
UNCHANGED

and eventually:

DELETED / NO LONGER ACCESSIBLE

For each page, we already get useful information from Notion such as:

page_id
last_edited_time

So we can maintain something like:

Our metadata store

| page_id | last_synced_at | source_updated_at |
| :------ | :------------- | :---------------- |
| A       | 10:00          | 09:55             |
| B       | 10:02          | 10:02             |
| C       | 10:03          | 09:40             |

When synchronization runs:

```text
Notion
│
▼
Discover pages
│
▼
Compare page metadata
│
├── New → process
├── Modified → process again
└── Unchanged → skip
```

For example:

Notion says:

Page A → last edited = 10:30
Our store → last processed = 10:00

10:30 > 10:00
↓
Re-process Page A

But:

Page B → last edited = 10:00
Our store → last processed = 10:00

same
↓
skip
One important architectural decision

We should not overwrite everything blindly when a page changes.

The lifecycle should eventually be:

Notion Page
↓
Retrieve latest version
↓
Parse
↓
Normalize
↓
Replace/update old representation
↓
Re-index

Because if the content changed, the old:

chunks
embeddings
keyword index entries
graph relationships

may no longer be valid.

So eventually a modification triggers something like:

```text
Page modified
│
▼
Re-extract
│
▼
Re-normalize
│
├── Update vector index
├── Update keyword index
└── Update graph
```

But don't implement synchronization yet

For our current stage, I would keep the problem separated:

Stage 1

Given page_id
↓
retrieve complete page
↓
parse
↓
normalize

Stage 2

Discover all accessible pages
↓
identify new/changed pages
↓
call Stage 1 for each

This is a very important design principle: the page processor should not care whether a page was discovered for the first time or because it changed. It simply receives a page ID and produces the latest representation.

Then the synchronization layer decides which page IDs need processing.

So yes, your understanding is correct: the final connector is a synchronization system, not just a single-page API client. The single-page flow we're building now is the core processing unit that the later sync mechanism will repeatedly invoke.

---

```text
                    Email Connector
                          │
             ┌────────────┴────────────┐
             ▼                         ▼
        GmailConnector          OutlookConnector
             │                         │
        Gmail API              Microsoft Graph
             │                         │
             └────────────┬────────────┘
                          ▼
                  Normalized Email
                          │
                          ▼
                         OKF
                          │
              ┌───────────┼───────────┐
              ▼           ▼           ▼
           Vector       Keyword      Graph
             DB           DB           DB
```

---

Don't just extract:

subject
body

For your Enterprise Knowledge Agent, an email should become something closer to:

```json
{
"id": "provider-specific-id",
"source": "gmail",
"thread_id": "...",

"subject": "Project Alpha Update",

"sender": {
"name": "John Smith",
"email": "john@example.com"
},

"recipients": [
{
"name": "Alice",
"email": "alice@example.com"
}
```

],

"cc": [],

"timestamp": "2026-08-23T10:30:00Z",

"body": "...",

"attachments": [],

"labels": ["INBOX"],

"metadata": {
"is_read": true
}
}

Then transform that into your OKF representation.

---

MIME representation most commonly refers to MIME types (Multipurpose Internet Mail Extensions) in computing, which tell a web browser or email client what kind of file or data it is receiving.Computing and Internet (MIME Type / Media Type)Definition: A standard way of describing the nature and format of a document, file, or piece of data (e.g., text/html, image/jpeg, or application/json).Structure: It uses a type and a subtype separated by a forward slash (type/subtype), such as audio/mpeg.Purpose: It helps software systems (like web servers and browsers) understand how to handle, display, or execute incoming files correctly.Performing Arts (Theatrical Mime)Definition: A form of silent art where an actor communicates a story, emotion, or character using only physical gestures, body movements, and facial expressions without speech.

---

![alt text](image.png)

---

1. First, what is this endpoint?

You are looking at:

GET https://gmail.googleapis.com/gmail/v1/users/{userId}/messages

Break it down:

https://gmail.googleapis.com

```text
│
└── Gmail API server
```

/gmail

```text
│
└── Gmail API
```

/v1

```text
│
└── API version 1
```

/users/{userId}
/messages

```text
│
└── resource being requested
```

So in plain English:

"Using version 1 of Google's Gmail API, give me the messages belonging to this user."

For our application:

GET /gmail/v1/users/me/messages

means:

"Give me the messages in the mailbox of the currently authenticated Gmail user."

Google specifically documents me as a special value representing the authenticated user.

2. What is v1?

v1 means:

Version 1 of the API.

It's an API version identifier, not a Python version, Gmail version, HTTP version, etc.

Think about an API as a contract between your application and Google.

Suppose Google initially creates:

/gmail/v1/...

and promises:

GET /gmail/v1/users/me/messages

works in a particular way.

Years later Google might need to introduce a fundamentally different API:

/gmail/v2/...

Now they can have:

/gmail/v1/users/me/messages

and

/gmail/v2/users/me/messages

coexist while applications migrate.

This is why you frequently see:

/api/v1/...

in REST APIs.

3. Why not just /gmail/users/me/messages?

Because APIs evolve.

Imagine your application is built in 2026:

Your Application
↓
Gmail API v1

Google changes the API in 2028.

If they completely changed the behavior of the existing endpoint, your application could suddenly break.

Instead, API versioning allows:

```text
                  Google API
                     │
            ┌────────┴────────┐
            ▼                 ▼
          v1 API            v2 API
            │                 │
       Old applications    New applications
```

The old contract can remain available while newer clients use the new contract.

4. Is v1 the first version ever?

Not necessarily.

This is an important distinction.

v1 usually means the first stable/public version of that particular API contract, not necessarily:

"Google has only ever had one version of this API."

APIs can have:

v1
v2
v3

and sometimes intermediate versions or revisions.

You should treat the version as part of the API's public interface.

5. Now let's understand the complete endpoint

You have:

GET https://gmail.googleapis.com/gmail/v1/users/{userId}/messages

There are several concepts here.

GET

This is the HTTP method.

It means:

"I want to retrieve something."

For example:

GET → retrieve
POST → create/send
PUT/PATCH → update
DELETE → delete

For our email connector, we'll mostly be using GET.

gmail.googleapis.com

This is the API server / host.

Your Python program sends an HTTP request to this server.

Conceptually:

```text
Your Python program
│
│ HTTPS request
▼
gmail.googleapis.com
│
▼
Gmail API
│
▼
Your mailbox
```

/gmail

This identifies the Gmail API service.

/v1

API version.

v1 = Version 1
/users/{userId}

This identifies whose mailbox we're talking about.

For example:

/users/me

or potentially:

/users/user@example.com

But in our application we'll use:

users/me

because we're accessing the mailbox belonging to the authenticated user.

/messages

This identifies the resource.

We're asking for Gmail messages.

So:

/users/me/messages

means:

messages belonging to the authenticated user.

6. What does {userId} mean?

The {} notation means:

This is a variable/path parameter.

The documentation says:

/users/{userId}/messages

But we don't literally send:

/users/{userId}/messages

We replace {userId}.

For our application:

/users/me/messages

The API then understands:

me → authenticated Gmail account

## 7. What are query parameters?

You also saw:

maxResults
pageToken
q
labelIds[]
includeSpamTrash

These are query parameters.

They come after ?.

For example:

GET https://gmail.googleapis.com/gmail/v1/users/me/messages?maxResults=5

Here:

?maxResults=5

is a query parameter.

You can have multiple:

?maxResults=5&includeSpamTrash=true

Conceptually:

```text
Endpoint
│
├── Path parameters
│
└── Query parameters
```

## 8. Why does Gmail have q?

This one is particularly useful for our connector.

Gmail API supports Gmail's search syntax.

For example:

q=from:alice@example.com

or:

q=subject:project

or:

q=after:2026/08/01

or:

q=from:alice@example.com after:2026/08/01

So our connector can do:

```python
service.users().messages().list(
userId="me",
q="after:2026/08/01",
maxResults=100
)
```

instead of downloading everything.

This will become very useful when we implement incremental/filtered ingestion.

9. What does maxResults do?

You saw:

Maximum number of messages to return. Default 100. Maximum 500.

So:

maxResults=5

means:

Give me at most 5 messages in this response.

That's what our test code is doing:

```python
messages().list(
userId="me",
labelIds=["INBOX"],
maxResults=5
)
```

## 10. But why does Gmail return only IDs?

This is a very important API design decision.

The response looks approximately like:

```json
{
"messages": [
{
"id": "abc123",
"threadId": "xyz123"
},
{
"id": "def456",
"threadId": "xyz456"
}
```

],
"nextPageToken": "...",
"resultSizeEstimate": 125
}

Gmail deliberately doesn't return the entire email for every message in list.

Instead:

messages.list()
↓
IDs
↓
messages.get(id)
↓
Actual email

This is much more efficient.

Imagine you have:

50,000 emails

and every email contains:

subject
body
attachments
headers
MIME parts

Returning all of that from a listing endpoint would be expensive.

So Google separates:

Discovery
messages.list()

from:

Retrieval
messages.get()

This is a very common API design pattern.

11. What is nextPageToken?

Suppose you have:

10,000 emails

but:

maxResults=500

You don't get 10,000 messages in one response.

You get:

Page 1
↓
500 messages
↓
nextPageToken

Then:

Page 2
↓
500 messages
↓
nextPageToken

and so on.

Conceptually:

```text
messages.list()
│
▼
┌──────────────┐
│ 500 messages │
└──────┬───────┘
│
▼
nextPageToken
│
▼
┌──────────────┐
│ 500 messages │
└──────┬───────┘
│
▼
nextPageToken
│
▼
...
```

This is called pagination.

We'll absolutely need to implement pagination in the real connector.

12. What are authorization scopes?

You saw:

https://mail.google.com/
https://www.googleapis.com/auth/gmail.modify
https://www.googleapis.com/auth/gmail.readonly
https://www.googleapis.com/auth/gmail.metadata

These determine what your application is allowed to do.

For our project:

gmail.readonly

is appropriate.

Think of OAuth scopes as permissions:

```text
Application
│
├── gmail.readonly
│ └── Read emails
│
├── gmail.modify
│ └── Read + modify emails
│
└── mail.google.com
└── Broad Gmail access
```

This is directly relevant to your Enterprise Knowledge Agent's RBAC/security architecture. Your system should request the minimum provider permissions necessary.

13. API version vs API library version

This is another thing that confuses people.

You might install:

pip install google-api-python-client

and see a Python package version such as:

2.x.x

That's not the same thing as:

/gmail/v1/

They are independent.

```text
google-api-python-client
│
└── Python client library version

Gmail API
│
└── /v1/ API version
```

Your Python library might be updated:

google-api-python-client 2.x → 3.x

while you're still using:

Gmail API v1

## 14. You'll see this everywhere

Once you start building connectors, you'll notice this pattern.

For example:

Gmail
/gmail/v1/...

GitHub
/api/v3/...

Microsoft Graph
/v1.0/...

The exact versioning strategy differs by API provider.

Microsoft Graph is particularly interesting because it has:

/v1.0/

and:

/beta/

where beta is used for features that aren't guaranteed to remain stable.

So API versioning is a fundamental REST/API design concept, not something specific to Gmail.

15. Relating this back to our connector

Our Gmail connector will eventually make requests like:

```text
Authentication
│
▼
GET /gmail/v1/users/me/messages
│
▼
message IDs
│
▼
GET /gmail/v1/users/me/messages/{id}
│
▼
raw Gmail message
│
▼
Parser
│
▼
EmailDocument
│
▼
OKF
```

And the nice thing is that you're now seeing why each part exists, rather than treating the Gmail Python SDK as a black box.

---

In Gmail, **Message ID** and **Thread ID** identify two different levels of an email conversation.

### Simple example

Suppose you send:

> **You:** Hi, can we discuss Project X?

Then your colleague replies:

> **Alice:** Sure, let's discuss it tomorrow.

Then you reply:

> **You:** Perfect.

Gmail represents this roughly as:

```text
Thread ID: T123
│
├── Message ID: M001
│   └── "Hi, can we discuss Project X?"
│
├── Message ID: M002
│   └── "Sure, let's discuss it tomorrow."
│
└── Message ID: M003
    └── "Perfect."
```

So:

- **Message ID** → identifies **one specific email**
- **Thread ID** → identifies the **entire conversation**

Google's `messages.list` response gives both `id` and `threadId`; the `id` identifies the individual message, while the `threadId` associates messages belonging to the same conversation.

---

## 1. Message ID

A Gmail message has its own unique ID:

```json
{
  "id": "18f2abc123",
  "threadId": "18f2abc000"
}
```

The `id` refers to **that particular email**.

For example:

```text
M001 → Original email
M002 → Reply
M003 → Reply to reply
```

Each one has a different message ID.

You use the message ID when you want to retrieve a specific email:

```python
service.users().messages().get(
    userId="me",
    id="18f2abc123"
).execute()
```

---

## 2. Thread ID

The `threadId` groups related messages into a conversation.

For example:

```text
Thread T123
│
├── M001
├── M002
├── M003
└── M004
```

All these messages can have:

```text
threadId = T123
```

while their:

```text
messageId
```

is different.

You can therefore think:

```text
Thread = conversation
Message = individual email
```

---

## 3. Why does Gmail need both?

Because these are different things.

Imagine you ask:

> "What did Alice say in the Project X conversation?"

You care about the **thread**.

But if you ask:

> "What exactly did Alice's second reply say?"

You care about the **specific message**.

So:

```text
                    Thread
                      │
          ┌───────────┼───────────┐
          ▼           ▼           ▼
       Message     Message      Message
         M1           M2           M3
```

---

## 4. How this matters for our Enterprise Knowledge Agent

This is actually quite important for our data model.

I would store both:

```json
{
  "message_id": "M002",
  "thread_id": "T123",
  "subject": "Project X",
  "sender": "alice@example.com",
  "body": "Sure, let's discuss it tomorrow."
}
```

Then our knowledge graph could represent:

```text
Alice
  │
  │ sent
  ▼
Message M002
  │
  │ belongs_to
  ▼
Thread T123
  │
  ├── Message M001
  ├── Message M002
  └── Message M003
```

This gives us useful retrieval possibilities.

### Message-level retrieval

> "What did Alice say?"

```text
Alice
 ↓
Messages
 ↓
M002
```

### Conversation-level retrieval

> "What was discussed about Project X?"

```text
Project X
 ↓
Thread T123
 ↓
M001 → M002 → M003
```

That's especially useful for your **Graph RAG** design.

---

## 5. One subtle point

A **thread isn't itself an email**.

It's a Gmail organizational concept that groups related messages.

So don't model it as:

```text
Thread = one email
```

Instead:

```text
Thread
   │
   ├── Email
   ├── Email
   ├── Email
   └── Email
```

For our connector, I'd therefore have:

```python
class EmailDocument:
    message_id: str
    thread_id: str
    ...
```

rather than only storing one ID.

### In one sentence

**Message ID identifies one email; Thread ID identifies the conversation to which that email belongs.**

For our connector, **we should store both**, because message-level retrieval and conversation-level/Graph RAG retrieval will need different granularities.

---

# GitHub Connector: From Repositories to Knowledge

This section captures the research and design decisions behind the **GitHub Connector**.

## What is a GitHub repository as a knowledge source?

A GitHub repository is not just:

```text
Repo
└── code: "some large codebase"
```

It has several distinct knowledge-bearing surfaces:

```text
Repository
│
├── Repository metadata
│   ├── name / full_name (owner/repo)
│   ├── description
│   ├── language
│   ├── license
│   ├── stars / forks / watchers
│   ├── open issue count
│   └── default branch
│
├── README           ← the primary human-readable documentation
├── Source files     ← the code itself (tree / file contents)
└── Issues / PRs     ← discussions, decisions, and history
```

For an Enterprise Knowledge Agent, the highest-signal, most retrievable surfaces are:

- **README** → documentation of intent, usage, architecture.
- **Repository metadata** → factual attributes (licensing, language, maintainership).
- **Issues / PRs** → why/what decisions were made, task tracking.

## GitHub API model: discovery vs. retrieval

Like Gmail, GitHub deliberately separates **discovery** from **retrieval**:

```text
GET /user/repos                 → a list of repos (metadata summary only)
    ↓
GET /repos/{owner}/{repo}       → full repo metadata
GET /repos/{owner}/{repo}/readme → base64 README
GET /repos/{owner}/{repo}/issues → issues / PRs
GET /repos/{owner}/{repo}/git/trees/{branch}?recursive=1 → file tree
```

Key points:

1. **Base URL**: `https://api.github.com` — the REST v3 API.
2. **Versioning header**: GitHub uses `X-GitHub-Api-Version: 2022-11-28` (an explicit API-version header rather than a `/vN/` path prefix). This is the same API-versioning idea we saw with Gmail's `/v1/` and Microsoft Graph's `/v1.0/`.
3. **Auth**: `Authorization: Bearer <token>`. A fine-grained PAT (Personal Access Token) with read-only `Contents`, `Metadata`, and `Issues` scopes is the least-privilege choice for a retrieval system.
4. **Pagination**: list endpoints return a `Link` header (`rel="next"`) rather than a `nextPageToken`. Client code must parse that header to page through all items.

## What to extract and preserve

Following the same principle as Notion — "don't throw away information too aggressively" — we keep:

```json
{
  "repo_id": "owner/repo",
  "title": "repo name",
  "url": "https://github.com/owner/repo",
  "description": "...",
  "language": "Python",
  "default_branch": "main",
  "stars": 10,
  "forks": 3,
  "license": "MIT",
  "created_at": "...",
  "updated_at": "...",
  "readme": "# ...",
  "issues": [ { "number": 1, "title": "...", "state": "open", "labels": [...] } ]
}
```

The normalized Document persists:

`repo_id` → stable source identifier
`url` → source citation
`updated_at` → synchronization later (last push / last issue update)
`readme` → primary knowledge body

## GitHub connector processing layers

```text
GitHub REST API JSON
        ↓
Repositories / README / Issues
        ↓
Fetch (client.py) selects surfaces by flags (fetch_readme, fetch_issues)
        ↓
Normalize (parser.py) → Document (typed Document/ContentBlock)
        ↓
OKF
```

The three-layer idea from Notion applies verbatim:

### 1. Repository-level information

Keep metadata: `full_name`, `url`, `description`, `language`, `license`, `default_branch`, stars/forks/issues, `updated_at`.

### 2. Content-level information (README + issues)

Convert README markdown into semantic blocks (headings, code fences, paragraphs). Convert each issue into metadata + numbered list entries (title, state, author, labels, body).

### 3. Preserve source information

Every block still knows it came from `github://owner/repo` (and, for issues, which issue number).

## Why separate fetch from normalization (again)

The same reason as Notion and Gmail:

```text
discover repos → fetch repo + readme + issues
        ↓
complete repo data
        ↓
normalize()
        ↓
our Document model
```

`normalize()` can assume the fetch layer already attempted to retrieve the complete repo surface, so it never has to know about pagination or Link-header parsing.

## Architecture overview

```text
GitHub Account
        │
        ▼
Discover accessible repos
        │
        ├── Repo A ── README + issues
        ├── Repo B ── README + issues
        └── Repo C ── README + issues
              │
              ▼
Normalize each
        │
        ▼
Knowledge Store (Document → OKF)
```

## Retrieval granularity

We want both repository-level and issue-level retrieval:

> "What does this project do?" → README + repo metadata

> "What issue mentioned X?" → specific issue

So we store the README as one document (with repo metadata in blocks) and represent issues structurally so they remain individually addressable.

### In one sentence

**A GitHub repo becomes a knowledge document that joins repository metadata, README documentation, and issue discussions — preserving ownership, licensing, and history for retrieval and Graph RAG.**

---

# Dropbox Connector: From Filesystem to Knowledge

This section captures the research and design decisions behind the **Dropbox Connector**.

## What is Dropbox as a knowledge source?

Dropbox is a cloud filesystem. Its content is organized as:

```text
Account
│
└── Root "/"
    ├── folder/
    │   ├── notes.md
    │   └── report.pdf
    └── readme.txt
```

So the natural knowledge granularity is **file** (for text documents) and **folder** (as a hierarchical container / index).

For an Enterprise Knowledge Agent, the valuable surface is:

- **Text files** → notes, markdown docs, plain text, code files.
- **Folders** → a structured index of what lives where (like a Notion database of contents).

## Dropbox API model: metadata vs. content

Dropbox splits its API into two hosts:

```text
https://api.dropboxapi.com/2       → JSON metadata endpoints (list, metadata, account)
https://content.dropboxapi.com/2   → file download endpoint
```

Key endpoints:

```text
POST /2/users/get_current_account  → account info (display name, email)
POST /2/files/list_folder          → entries in a folder (cursor-paginated, recursive option)
POST /2/files/get_metadata         → single file/folder metadata
POST /2/files/download             → file bytes (content host)
```

Key points:

1. **Auth**: `Authorization: Bearer <token>` — a Dropbox access token (from https://www.dropbox.com/developers/apps).
2. **Discovery vs. retrieval**: `list_folder` returns _metadata only_ (name, path, size, modified). To get _content_ you must call `download` for each text file. This mirrors Gmail's `list` (IDs) → `get` (payload) split.
3. **Recursive listing**: `list_folder` accepts `recursive: true`, so we can walk the whole account tree in one call rather than recursing manually.
4. **Cursor pagination**: `list_folder` returns `has_more` + `cursor`; pass the cursor to `list_folder/continue` until `has_more` is false — exactly the Notion `next_cursor` pattern.
5. **`Dropbox-API-Result` header**: on content downloads, file metadata is returned in an HTTP _header_ (JSON) while the body is the raw file bytes — the client must parse both.

## What to extract and preserve

```text
File
├── path_lower (e.g. /docs/notes.md)
├── name
├── size (bytes)
├── server_modified (last edited)
├── client_modified
└── content (downloaded text, if text file)

Folder
├── path_lower
├── name
└── child entries: [{name, path, size, modified}]
```

The normalized Document persists:

`path` → stable source identifier (like page_id)
`server_modified` → synchronization / recency
`content` → the knowledge body (for text files)
`child entries` → structured folder index table

## Text vs. binary filtering

Not every file is retrievable knowledge:

```text
notes.md        → text  ✅ extract
report.pdf      → binary (PDF) → currently skipped (future OCR/multimodal extractor)
image.png       → binary → skipped
some.locked.tmp → ignored name → skipped
```

So the parser defines extension allow/block lists (`PREFERRED_TEXT_EXTENSIONS`, `IGNORED_TEXT_EXTENSIONS`) and ignored file names (`IGNORED_FILE_NAMES`). This keeps the pipeline free of low-signal binary noise — the same principle as Notion's `IGNORED_BLOCK_TYPES`.

## Dropbox connector processing layers

```text
Dropbox API JSON + file bytes
        ↓
List folders (recursive) + download text files
        ↓
Fetch (client.py): list_folder, get_metadata, download
        ↓
Normalize (parser.py) → File Document / Folder Document
        ↓
OKF
```

The three-layer idea, applied:

### 1. File-level information

Keep `path`, `name`, `size`, `server_modified`, `client_modified`.

### 2. Content-level information

For text files, the downloaded content becomes `PARAGRAPH` / heading blocks (markdown preserved). For folders, a structured `database` block ("Folder Contents") lists every contained file/subfolder (Name, Path, Size, Modified) — like a Notion table.

### 3. Preserve source information

Every block knows it came from `dropbox://{path}` so citations and updates stay traceable.

## Why separate fetch from normalization (again)

```text
list folders → fetch metadata → download text files
        ↓
complete tree + content
        ↓
normalize()
        ↓
our Document model
```

The fetch layer walks the tree once; `normalize()` converts each file/folder dict into a typed Document. The connector orchestrates both while honoring `root_path`, `include_folders`, `include_files`, and `max_files` (to bound download volume).

## Architecture overview

```text
Dropbox Account
        │
        ▼
Root path (recursive folder listing)
        │
        ├── Folder → Folder Document (structured contents table)
        ├── notes.md → File Document (content blocks)
        ├── report.pdf → skipped (binary)
        └── image.png → skipped (binary)
              │
              ▼
Normalize each
        │
        ▼
Knowledge Store (Document → OKF)
```

## Sync consideration

`server_modified` gives us the delta signal for incremental synchronization (re-process a file only when `server_modified > last_synced`). This mirrors the `last_edited_time`-based sync in Notion/GitHub.

### In one sentence

**Dropbox becomes a hierarchical knowledge source where folders act as structured indexes and text files act as documents — while binary files are filtered out to keep the pipeline clean.**

---

8. Why offline matters

This is particularly important for your connector.

You don't want the user to log into Dropbox every time your ingestion pipeline runs.

For example:

Monday
↓
Sync Dropbox

Tuesday
↓
Sync Dropbox

Wednesday
↓
New Dropbox file
↓
Sync automatically

Therefore you want a refresh token.

Dropbox supports short-lived access tokens together with refresh tokens for offline access.

So your final credentials are conceptually:

App Key

- App Secret
- Refresh Token

Your backend can then obtain/refresh short-lived access tokens when needed.

The current Dropbox JavaScript SDK also explicitly supports access tokens, refresh tokens, client IDs, and client secrets.

---

# Confluence Connector: From Pages to Knowledge

## What is Confluence as a knowledge source?

Atlassian Confluence is the enterprise wiki layer of the knowledge stack. It organizes
high-signal documentation ("pages") into **spaces** (e.g. Engineering, Product, HR).
Each page carries a Storage-Format XHTML body, a version history, and an ancestor chain
that encodes the wiki hierarchy.

## Confluence API model: discovery vs. retrieval

- **Discovery** (`GET /rest/api/space` → `GET /rest/api/content?spaceKey=...`): find every
  space and every page the token account can read. Confluence returns `_links.next` URLs,
  not opaque cursors, for pagination.
- **Retrieval** (`GET /rest/api/content/{id}?expand=body.storage,version,space,history,ancestors`):
  fetch a full page with its rendered Storage-Format body, version metadata, space, creation
  history, and the parent page chain in a single request.

## What to extract and preserve

1. **Space-level metadata** — space key/name define the knowledge domain boundary.
2. **Page-level metadata** — title, space key, ancestor page IDs (hierarchy), version number,
   created/last-edited timestamps, creator/editor.
3. **Content structure** — the Storage-Format XHTML is *not* plain text: headings, paragraphs,
   bullet/numbered lists (with nesting), tables (→ structured DATABASE records), code blocks,
   blockquotes, and dividers must be preserved as semantic blocks.

## Text vs. rich structure parsing

Unlike Notion's JSON blocks, Confluence bodies arrive as storage XHTML. We parse the DOM
with a lightweight `html.parser`-based tree builder that preserves **document order** (text
segments interleaved with child elements). Inline tags (`strong`, `em`, `<a href>`, `<code>`)
fold into readable text; links render as `Text (https://...)`; images/media/emoticons are
ignored via an explicit ignore-list; `ac:structured-macro` panels flatten to their inner text.

## Confluence connector processing layers

### 1. Page-level information
Title, space key, parent page, timestamps, version, and URL drive indexing and recency scoring.

### 2. Content-level information
Storage XHTML → semantic `ContentBlock`s: headings, paragraphs, lists with nested children,
code blocks, quotes, and tables mapped to `DATABASE` blocks so tabular knowledge is retrievable.

### 3. Preserve source information
Every block carries the page ID, so downstream chunkers can cite `confluence://pages/{id}`.

## Why separate fetch from normalization (again)

The Confluence REST client handles paging, `/wiki` base-path detection, and Basic-auth;
`parser.py` is a pure DOM→blocks converter. This keeps the network layer dumb and the
normalization layer testable with static HTML fixtures.

## Sync consideration

`version.when` (last edit timestamp) drives incremental synchronization —
re-ingest a page only when `version.when > last_synced_time`.

### In one sentence

**Confluence becomes a wiki knowledge source where spaces act as domain boundaries,
ancestor chains as hierarchy, and Storage-Format bodies as semantic documents — with
tables preserved as structured records rather than flattened text.**

---

# Jira Connector: From Issues to Knowledge

## What is Jira as a knowledge source?

Jira is the operational/task truth layer of an enterprise: every piece of work is an
**issue** (bug, task, story, epic) scoped to a **project**. Issue descriptions use the
Atlassian Document Format (ADF) — a JSON AST, not HTML or text — and must be parsed
structurally.

## Jira API model: discovery vs. retrieval

- **Discovery** (`GET /rest/api/3/project`): list all projects visible to the token account.
- **Search/Retrieval** (`GET /rest/api/3/search?jql=...&startAt=...` and
  `GET /rest/api/3/issue/{key}`): JQL paging uses the `startAt`/`maxResults` window model
  (10, 20, 30 …), not cursors, so retrieval is idempotent and resumable.

## What to extract and preserve

1. **Project-level metadata** — key + name define the work domain boundary.
2. **Issue-level metadata** — key, summary, project, issue type, status, priority,
   assignee, reporter, labels, components, created/updated timestamps.
3. **Description structure** — the ADF JSON tree: headings, paragraphs (with `strong`/`em`/
   `code` marks and mentions), bullet/numbered lists with nesting, code blocks, tables
   (→ structured records), and rules (dividers).

## ADF: parsing JSON, not HTML

ADF nodes are recursive `{type, attrs, content}` objects. We walk the tree depth-first:
- `text` inline nodes fold into concatenated strings; marks apply formatting that is
  discarded for retrieval but preserved in Markdown where trivial.
- `bulletList`/`orderedList`/`listItem` map to nested content blocks.
- `table` → `tableHeader`/`tableCell` map to a `DATABASE` block with columns + rows.
- `mention`/`emoji` render as readable inline text (`@handle`).
- Unknown node types are skipped gracefully so one-off ADF nodes never crash ingestion.

## Jira connector processing layers

### 1. Project-level information
Key + name establish the record; a lightweight project overview doc is generated on request.

### 2. Issue-level information
Summary, status, assignee, labels, and the full parsed ADF description become a document
whose body carries retrievable operational detail.

### 3. Preserve source information
Every block is traceable to the issue key and `jira://issues/{key}` for citations.

## Why separate fetch from normalization (again)

The Jira client owns auth, JQL paging, and per-issue field selection; `parser.py` is a pure
ADF→blocks converter, testable with static ADF fixture JSON.

## Sync consideration

`fields.updated` gives the delta signal for incremental synchronization —
re-process an issue only when `updated > last_synced_time`.

## Credential & URL checklist (pitfalls found during live testing)

1. `JIRA_URL` must point to the **actual Jira site the API token was issued for**
   (e.g. `https://your-org.atlassian.net`). Setting it to the generic account portal
   `https://home.atlassian.com` is a silent killer: that host answers with the *Atlassian
   Home* HTML page at HTTP 200, so `test_connection()` passes while every `response.json()`
   call throws `Expecting value: line 1 column 1`.
2. `JIRA_USERNAME` must be the exact account e-mail linked to the API token, not an
   employee ID or display name.
3. An expired/revoked API token yields HTTP 401 `Client must be authenticated` on
   `/rest/api/3/myself` — regenerate at [id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens).
4. The connection test (`GET /rest/api/3/myself`) only checks the HTTP status code.
   Guard against HTML/portal responses by validating `Content-Type: application/json`
   when the test is used to gate downstream ingestion.

### In one sentence

**Jira becomes a task knowledge source where projects act as domains and issues as
documents — descriptions parsed from ADF keep nested structure and tables so operational
knowledge stays retrievable.**

---
