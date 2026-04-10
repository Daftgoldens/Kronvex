# Contributing to Kronvex

Thank you for your interest in contributing!

## Local Setup

### Prerequisites
- Docker + Docker Compose
- Python 3.12+
- Node.js 18+ (for the TypeScript SDK)

### 1. Clone and configure

```bash
git clone https://github.com/kronvex-io/kronvex.git
cd kronvex
cp .env.example .env
# Edit .env: add your OPENAI_API_KEY
```

### 2. Start the stack

```bash
docker-compose up --build
```

This starts:
- PostgreSQL 16 with pgvector on port 5432
- Kronvex API on port 8000

### 3. Run tests

```bash
pip install -r requirements.txt
pytest tests/ -v
```

### 4. Interactive API docs

Open http://localhost:8000/docs

---

## Conventions

### Commit messages

We use [Conventional Commits](https://www.conventionalcommits.org/):

```
feat(memory): add TTL support for episodic memories
fix(recall): handle empty embedding edge case
docs(sdk): add LangChain integration example
chore(deps): bump fastapi to 0.115
```

Types: `feat`, `fix`, `docs`, `chore`, `test`, `refactor`

### Code style

- Python: follow existing patterns, no external formatter required
- TypeScript: `npm run build` must pass in `sdk/node/`

---

## Submitting a PR

1. Fork the repo and create a branch: `git checkout -b feat/your-feature`
2. Make your changes and add tests where relevant
3. Run `pytest tests/ -v` — all tests must pass
4. Update `CHANGELOG.md` under `[Unreleased]`
5. Open a PR using the template

---

## Questions?

Open an issue or email [hello@kronvex.io](mailto:hello@kronvex.io).
