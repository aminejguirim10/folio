<div align="center">

# 📚 Folio

### Intelligent Document Research Assistant

Upload your documents, ask questions, and get reliable, contextualized, and verifiable answers.

<p>
  <img src="https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=white" alt="React 19">
  <img src="https://img.shields.io/badge/TypeScript-5.7-3178C6?logo=typescript&logoColor=white" alt="TypeScript">
  <img src="https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white" alt="FastAPI">
  <img src="https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/Google%20Gemini-AI-4285F4?logo=google&logoColor=white" alt="Google Gemini">
  <img src="https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white" alt="Docker">
</p>

<p>
  <a href="#-quick-start">🚀 Quick start</a> ·
  <a href="#-features">✨ Features</a> ·
  <a href="#-architecture">🏗️ Architecture</a> ·
  <a href="#-api">🔌 API</a>
</p>

</div>

---

## 🎯 About

**Folio** is a document question-answering application built around the **RAG (Retrieval-Augmented Generation)** pattern. It turns heterogeneous documents into a searchable knowledge base, retrieves the most relevant passages for each question, and generates an answer with **Google Gemini**.

It is designed for research, summarization, and exploration of personal or professional documents while reducing unsupported answers through hybrid search and source citations.

> **Folio's goal:** make the information in your documents immediately useful without losing the connection to the original sources.

## ✨ Features

| | Capability | Description |
|:---:|---|---|
| 📥 | **Multi-format ingestion** | PDF, DOCX, PPTX, XLSX/XLSM, CSV/TSV, TXT, Markdown, HTML, JSON, and images |
| 🔎 | **Hybrid search** | Combines ChromaDB vector search with BM25 lexical search |
| 🤖 | **Contextual answers** | Generates answers from the passages actually retrieved |
| 📌 | **Verifiable citations** | Document, page or location, excerpt, and relevance score |
| ⚡ | **Real-time streaming** | Progressively displays answers through Server-Sent Events |
| 🎯 | **Targeted search** | Select one or more documents before asking a question |
| 👁️ | **Document previews** | Browse original files and indexed chunks from the interface |
| 🖼️ | **Multimodal vision** | Transcribes scans, screenshots, and photos with Gemini |
| 🗂️ | **Document management** | List, re-index, replace, and delete documents |
| 🛡️ | **Built-in controls** | File validation, upload limits, rate limiting, and CORS |
| 💾 | **Docker persistence** | Keeps uploads, the catalog, and the vector index persistent |

## 🧰 Technology stack

| Layer | Technologies |
| --- | --- |
| Frontend | React 19, TypeScript, Vite, Tailwind CSS, Radix UI, Framer Motion |
| Backend | Python 3.12, FastAPI, Pydantic v2, Uvicorn |
| AI | Google Gemini for embeddings, generation, and image transcription |
| Search | ChromaDB for vector search and BM25 for lexical search |
| Ingestion | PyMuPDF, python-docx, python-pptx, openpyxl, and dedicated loaders |
| Deployment | Docker, Docker Compose, Nginx |

## ✅ Requirements

- Docker and Docker Compose **or**:
  - Python 3.12+
  - Node.js 20+
  - a Google Gemini API key
- LibreOffice is recommended for Office document conversion and previews in the backend Docker image.

## 🚀 Quick start

### 1. 📥 Clone the repository

```powershell
git clone https://github.com/aminejguirim10/folio.git
cd folio
```

> If your repository URL is different, replace the URL with the correct GitHub address.

### 🐳 Option A — Docker Compose

#### 2. ⚙️ Configure environment variables

Docker Compose uses two separate `.env` files: one for the backend API and one for the frontend. Example files are included in the project.

```powershell
Copy-Item backend\.env.example backend\.env
Copy-Item frontend\.env.example frontend\.env
```

At minimum, add your Google Gemini key to `backend\.env`:

```dotenv
GEMINI_API_KEY=votre_cle_api_gemini
```

The `backend\.env.example` file contains the complete backend configuration:

```dotenv
GEMINI_API_KEY=

CHAT_MODEL=gemini-2.5-flash
CHAT_FALLBACK_MODEL=gemini-flash-latest
EMBEDDING_MODEL=gemini-embedding-001
EMBEDDING_DIMENSIONS=768

MAX_UPLOAD_MB=25
MAX_UPLOAD_PAGES=1000
MAX_DOCUMENTS=50

TOP_K=6
CANDIDATE_K=24
MIN_RELEVANCE=0.30
MIN_LEXICAL_SCORE=6.0
ENABLE_MMR=false

CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
RATE_LIMIT_PER_MINUTE=30
```

The `frontend\.env.example` file configures the API URL:

```dotenv
VITE_API_URL=http://127.0.0.1:8000
```

> Keep the `.env.example` files as templates and never commit `.env` files containing secrets.

#### 3. ▶️ Start the services

```powershell
docker compose up --build
```

#### 4. ✅ Verify the installation

In a second terminal:

```powershell
Invoke-RestMethod http://localhost:8000/api/health
```

Then open the following interfaces:

- Application: <http://localhost:5173>
- Interactive API documentation: <http://localhost:8000/docs>
- API health: <http://localhost:8000/api/health>

To stop the containers:

```powershell
docker compose down
```

Application data is stored in the `rag-data` Docker volume.

### 💻 Option B — Local development

#### 🐍 Backend

```powershell
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Then add your key to `backend\.env` and start the API:

```powershell
uvicorn app.main:app --reload --port 8000
```

#### ⚛️ Frontend

In a second terminal:

```powershell
cd frontend
npm ci
Copy-Item .env.example .env
npm run dev
```

The interface is available at <http://localhost:5173>. Use `VITE_API_URL` to change the backend URL.

## ⚙️ Configuration

Backend configuration is available in [`backend/.env.example`](./backend/.env.example). The most important settings are:

| Variable | Description | Default |
| --- | --- | --- |
| `GEMINI_API_KEY` | Gemini access key | Required |
| `CHAT_MODEL` | Generation model | `gemini-2.5-flash` |
| `EMBEDDING_MODEL` | Embedding model | `gemini-embedding-001` |
| `MAX_UPLOAD_MB` | Maximum file size | `25` |
| `MAX_UPLOAD_PAGES` | Maximum units/pages per document | `1000` |
| `MAX_DOCUMENTS` | Maximum indexed documents | `50` |
| `TOP_K` | Number of chunks retained for an answer | `6` |
| `CORS_ORIGINS` | Allowed frontend origins, comma-separated | `localhost:5173`, `127.0.0.1:5173` |
| `RATE_LIMIT_PER_MINUTE` | Maximum questions per client per minute | `30` |
| `API_KEY` | Optional API protection key | Empty |
| `DATA_DIR` | Upload and index directory | `backend/data` |

Never commit `.env` files. Runtime data is intentionally ignored by Git.

## 🧭 Usage

1. Open Folio and upload one or more documents.
2. Wait for indexing to complete.
3. Ask a question in natural language.
4. Review the answer and open citations to verify the source passages.
5. Use document selection to restrict the search scope.

If no sufficiently relevant passage is found, Folio explains that it cannot answer from the available documents instead of fabricating a response.

## 🏗️ Architecture

```text
┌──────────────────────┐       HTTP / SSE        ┌─────────────────────────┐
│ React + Vite          │ ◄────────────────────► │ FastAPI                  │
│ Chat interface       │                         │ Document / chat routes  │
└──────────────────────┘                         └───────────┬─────────────┘
                                                            │
                                      ┌─────────────────────┼─────────────────────┐
                                      │                     │                     │
                              ┌───────▼────────┐  ┌────────▼────────┐  ┌────────▼────────┐
                              │ Ingestion       │  │ Retrieval       │  │ Gemini          │
                              │ loaders         │  │ ChromaDB + BM25 │  │ generation      │
                              │ chunking        │  │ citations       │  │ embeddings      │
                              └────────────────┘  └─────────────────┘  └─────────────────┘
```

### 📥 Indexing flow

1. The backend validates the file extension, signature, and size.
2. The loader extracts content and metadata (page, slide, sheet, or range).
3. The content is split into overlapping chunks.
4. Embeddings are generated and stored in ChromaDB.
5. The catalog stores the metadata required to display and manage documents.

### 💬 Question-answering flow

1. The question is validated and converted into a vector representation.
2. ChromaDB and BM25 results are merged and ranked.
3. A relevance threshold prevents generation when no reliable context is found.
4. Gemini generates an answer from the selected context.
5. The API returns citations and can progressively stream the answer through SSE.

## 🔌 API

All application routes are prefixed with `/api`.

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Service, Gemini provider, and index health |
| `GET` | `/documents/formats` | Supported formats and available dependencies |
| `GET` | `/documents/limits` | Upload limits exposed to the interface |
| `POST` | `/documents` | Upload and index a document |
| `GET` | `/documents` | List indexed documents |
| `GET` | `/documents/{doc_id}/preview` | Browse indexed chunks |
| `GET` | `/documents/{doc_id}/file` | Retrieve the original file |
| `GET` | `/documents/{doc_id}/preview.pdf` | Retrieve a PDF preview |
| `DELETE` | `/documents/{doc_id}` | Delete a document and its index |
| `POST` | `/ask` | Return a complete answer |
| `POST` | `/ask/stream` | Stream an answer through SSE |
| `POST` | `/feedback` | Record user feedback |
| `GET` | `/feedback/summary` | Retrieve feedback summary |

The OpenAPI specification is available at `/docs` when the backend is running.

## 🧪 Testing and quality

### 🐍 Backend

```powershell
cd backend
pytest
ruff check .
```

### ⚛️ Frontend

```powershell
cd frontend
npm run typecheck
npm run build
```

## 📁 Project structure

```text
folio/
├── backend/
│   ├── app/
│   │   ├── api/                 # Routes FastAPI
│   │   ├── core/                # Security and cross-cutting controls
│   │   ├── models/              # Pydantic schemas
│   │   └── services/             # Ingestion, search, Gemini, catalog
│   ├── .env.example
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── components/          # Interface and UI components
│   │   └── lib/                 # API client and utilities
│   ├── Dockerfile
│   └── package.json
├── docker-compose.yml
└── README.md
```

## 🔐 Security and operations

- Keys and secrets are read from the environment and must never be stored in the repository.
- File names are sanitized before storage and file signatures are validated.
- Uploads and question lengths are restricted.
- Rate limiting protects question-answering routes.
- Backend API protection can be enabled with `API_KEY`.
- Imported HTML files are served as downloads or in an isolated context to prevent scripts from running in the application origin.
- For production, use HTTPS, a trusted proxy, and explicit CORS origins.