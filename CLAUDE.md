# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A GraphRAG system for computer networking knowledge. Neo4j stores a hierarchical knowledge graph (5 OSI layers → entities). A LangGraph ReAct Agent with multiple tools answers user questions, using hybrid semantic + keyword retrieval.

**Tech Stack**: FastAPI + LangGraph + LangChain + Neo4j + ZhipuAI (LLM) + Qwen (Embeddings)

## Common Commands

```bash
pip install -r requirements.txt

# Web application (default: http://localhost:5000)
python run.py --mode web
python run.py --mode web --host 0.0.0.0 --port 5000 --debug

# CLI / test
python run.py --mode cli
python run.py --mode test

# Data import
python scripts/build_from_json.py              # Import from data/layers/*.json
python scripts/build_index_from_docs.py        # Build from PDFs (Microsoft GraphRAG pipeline)
python scripts/generate_embeddings.py          # Generate Qwen embeddings for Entity nodes
```

## Environment Setup

Create `.env` with:

```bash
# Neo4j (required)
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your-password

# ZhipuAI LLM (required)
ZHIPUAI_API_KEY=sk-your-api-key
ZHIPUAI_MODEL=glm-4-flash

# Qwen Embeddings (required for semantic search)
QWEN_API_KEY=sk-your-api-key
QWEN_EMBEDDING_MODEL=text-embedding-v3

# Web
WEB_HOST=0.0.0.0
WEB_PORT=5000
DEBUG=false
```

## Architecture

```
User Question
    │
    ▼
FastAPI (/api/query)  ←──  web/graph_rag_web.py
    │
    ▼
GraphRAGAgent  ←──  src/graphrag_agent.py
  LangGraph ReAct Agent with MemorySaver
  LLM (ZhipuAI) autonomously picks tools:
    ├── knowledge_search   → Neo4jGraphRetriever._search()
    ├── graph_visualize    → retriever.get_graph_data_for_visualization()
    ├── graph_statistics   → retriever.get_graph_stats()
    ├── node_search        → retriever.keyword_search()
    └── node_neighbors     → retriever.get_neighbors()
    │
    ▼
Neo4jGraphRetriever  ←──  src/langchain_retriever.py
  Hybrid retrieval:
    1. _semantic_search()  → EmbeddingManager.embed_query() + Neo4j vector index
    2. _keyword_search()   → regex keyword extraction + Cypher CONTAINS
    3. Merge & rank → _build_context() with entities, Q&A, relationships, layers
    │
    ▼
Neo4j Graph DB
  Labels: Layer, Entity (with sub-labels: Protocol, Device, Concept), Question, Answer
  Vector index: entity_embedding_index (cosine, 1024-dim, Qwen text-embedding-v3)
```

### Key Components

| Component | File | Purpose |
|-----------|------|---------|
| GraphRAGAgent | `src/graphrag_agent.py` | ReAct Agent with tools, session memory via MemorySaver |
| Neo4jGraphRetriever | `src/langchain_retriever.py` | Hybrid retrieval (semantic + keyword), graph visualization data |
| EmbeddingManager | `src/embedding_manager.py` | Qwen text-embedding-v3 via DashScope API |
| LangGraphRAGConfig | `src/langchain_config.py` | Neo4j, LLM, and retrieval parameter configuration |
| QualityMonitor | `src/quality_monitor.py` | Quality metrics, alerts, and trend analysis |
| KnowledgeGraphImageGenerator | `src/image_generator.py` | Graphviz-based graph visualization |

### Important: Module-level Retriever Reference

Tools in `graphrag_agent.py` are `@tool`-decorated functions (not methods). They access the retriever via a module-level `_retriever` variable set in `GraphRAGAgent.__init__`. This is how LangChain tools share state with the agent.

## Data Model

**Neo4j node labels**: `Layer`, `Entity` (with sub-labels `Protocol`, `Device`, `Concept`), `Question`, `Answer`

**Key relationships**: `CONTAINS` (Layer→Entity), `ABOUT` (Question→Entity), `RESPONDS_TO` (Answer→Question), plus domain relationships like `DEPENDS_ON`, `WORKS_WITH`, `APPLY_TO`

**Entity properties**: `name`, `entity_type`, `description`, `layer`, `embedding` (vector, 1024-dim)

**Data source**: `data/layers/{1_physical,2_data_link,3_network,4_transport,5_application}.json` + `cross_layer.json`

## Retrieval Flow in Detail

1. **User question** → LLM decides to call `knowledge_search` tool
2. **`_retrieve_entities(question)`** — hybrid retrieval:
   - `_semantic_search()`: embed query → `db.index.vector.queryNodes('entity_embedding_index', ...)` → top-10 by cosine similarity
   - `_keyword_search()`: extract keywords → Cypher `CONTAINS` matching → scored results
   - Merge, deduplicate, sort by score
3. **`_get_qa_for_entities(entity_names)`** — look up related Question+Answer nodes
4. **`_get_layers_for_entities(entity_names)`** — get parent Layer info
5. **`_build_context()`** — assemble all into structured text → returned as `Document`
6. Agent LLM reads context and generates answer

## Frontend

Single-page app: `templates/integrated_index.html` + `static/js/integrated-app.js` + `static/js/neo4j-graph.js` (D3.js visualization). Legacy files (`app.js`, `app_fixed.js`, `graph-performance.js`, `style.css`) have been removed.

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/query` | POST | Main query (`{question, session_id?}`) |
| `/api/sessions` | GET | List sessions |
| `/api/sessions/new` | POST | Create session |
| `/api/sessions/{id}/history` | GET | Session history |
| `/api/sessions/{id}` | DELETE | Delete session |
| `/api/graph_stats` | GET | Graph statistics |
| `/api/search_nodes` | GET | Search nodes (`?q=keyword`) |
| `/api/node_neighbors/{name}` | GET | Node neighbors |
| `/api/export_graph` | POST | Export subgraph |
| `/api/generate_image` | POST | Graphviz image |
| `/api/quality_report` | GET | Quality report |
| `/api/quality_alerts` | GET | Quality alerts |
| `/api/quality_trends` | GET | Quality trends |
| `/api/config` | GET | Feature flags |
| `/api/health` | GET | Health check |
| `/docs` | GET | Swagger docs |
