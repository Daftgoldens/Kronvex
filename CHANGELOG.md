# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-04-10

### Added
- Core memory API: `/remember`, `/recall`, `/inject-context` endpoints
- Confidence scoring: `similarity × 0.6 + recency × 0.2 + frequency × 0.2`
- pgvector cosine similarity search with 1536-dim embeddings (text-embedding-3-small)
- API key authentication with SHA256 hashing (`kv-*` prefix)
- Agent management: create, list, delete agents
- Memory management: store, recall, delete memories
- Demo key endpoint for free tier access
- Stripe billing integration (Builder / Startup / Business / Enterprise)
- Onboarding email sequences (J+1, J+3, J+7)
- Python SDK (sync + async, published on PyPI)
- Node/TypeScript SDK (ESM + CJS, published on npm)
- MCP server for Claude Desktop integration
- Docker Compose setup for local development
- EU data residency (Frankfurt, Supabase)
- GDPR-compliant data processing
