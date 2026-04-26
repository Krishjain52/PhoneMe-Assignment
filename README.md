# Incident Response System — 3-Agent Pipeline

A working multi-agent system that ingests production logs, diagnoses the root cause, researches solutions from authoritative sources, and produces a step-by-step remediation runbook.

---

## Quick Start

### 1. Prerequisites

- Python 3.11+
- A Groq API key — get one free at https://console.groq.com

### 2. Install dependencies

```bash
pip install groq
```

### 3. Set your API key

```bash
export GROQ_API_KEY="gsk_..."
```

### 4. Place log files

The system expects logs at:

```
logs/
  app-error.log
  nginx-error.log
  nginx-access.log
```

The provided log files are already in place.

### 5. Run

```bash
python main.py
```

Optional flags:
```bash
python main.py --quiet    # suppress per-agent progress output
```

### 6. Outputs

After a successful run, `output/` will contain:

| File | Contents |
|------|----------|
| `agent1_output.json` | Root cause analysis + structured handoff |
| `agent2_output.json` | Solution options + source excerpts |
| `agent3_output.json` | Remediation runbook (structured) |
| `incident_report.md` | Human-readable full incident report |

---

## Architecture

### Agent Boundaries

| Agent | Responsibility | LLM? |
|-------|---------------|------|
| **Agent 1 — Log Analysis** | Reads all 3 log files; uses Groq (`llama-3.3-70b-versatile`) to identify root cause, extract evidence, and assign confidence | ✅ Groq LLM |
| **Agent 2 — Solution Research** | Fetches authoritative docs via HTTP (SQLAlchemy, PostgreSQL, Gunicorn, Nginx official docs); extracts relevant excerpts; builds structured solution options using heuristics — **no LLM** | ❌ HTTP scraping only |
| **Agent 3 — Resolution Planner** | Receives Agent 1 + Agent 2 outputs; uses Groq (`llama-3.3-70b-versatile`) to select the safest solution and write operator-ready runbook with pre/post checks | ✅ Groq LLM |

### Handoff Format

Each agent produces a JSON object. The pipeline passes these directly as Python dicts (in-process) and also persists them to `output/agentN_output.json` for inspection.

Key handoff fields:

- **Agent 1 → Agent 2:** `handoff_for_agent2` containing `problem_type`, `stack`, `specific_error_codes`, `search_keywords` — used by Agent 2 to select relevant documentation sources.
- **Agent 2 → Agent 3:** `handoff_for_agent3` containing `top_solution_ids`, `confirmed_root_cause`, `deploy_version_implicated`, `code_path` — used by Agent 3 to focus its planning.

### Why These Implementation Choices Are Production-Reasonable

1. **Claude for analysis and planning, not research:** LLMs excel at synthesising unstructured text (logs) and generating structured instructions. They are poor at reliably citing current, versioned documentation. Agent 2 therefore uses real HTTP retrieval from official sources.

2. **Structured JSON handoffs:** Each agent outputs validated JSON with a known schema. This makes the pipeline debuggable — you can re-run any single agent from its persisted input file.

3. **Curated source registry:** Agent 2 uses a curated registry of known-reliable technical sources (SQLAlchemy docs, PostgreSQL runtime config, Gunicorn settings, Nginx proxy module) rather than a general web search, avoiding low-quality or SEO-spam results.

4. **Risk flags built in:** Agent 2 explicitly flags risky mitigations to avoid (e.g. `pg_terminate_backend`, `pool_timeout=0`). Agent 3 is prompted to prioritise safety.

5. **No secrets in code:** The Groq API key is read from the `GROQ_API_KEY` environment variable. No key is hardcoded.

---

## What the System Concluded for This Incident

The system identified a **P1 database connection pool exhaustion** incident caused by a **session leak introduced in deployment `2026.03.17-2`**.

Specifically:
- `portfolio/rebalance_service.py:118` was modified in the 11:34 deploy and does not close its SQLAlchemy session on error paths.
- Starting at ~11:40, connections stopped returning to the pool.
- At 11:41, the pool was fully exhausted (25 checked out, 0 idle, 41+ waiters).
- All DB-dependent endpoints then began returning 504/502 errors.
- Two Gunicorn workers timed out and were recycled.

**Recommended fix:** Roll back `2026.03.17-2` immediately (stops the leak in ~2 minutes), then apply a permanent code fix wrapping `SessionLocal()` in a context manager.

---

## Limitations & Assumptions

- Agent 2's source fetching requires outbound internet access. If the environment is air-gapped, the excerpts will show `(unavailable)` but the solution list is still populated from engineering knowledge encoded in the agent.
- The system processes the logs as a static snapshot — it does not tail live log streams.
- Agent 3's runbook assumes systemd-managed gunicorn (`systemctl restart gunicorn`); adjust commands for other process managers (supervisord, Docker, etc.).
- No authentication or multi-tenancy is implemented — this is a single-operator CLI tool.
- The pipeline has no retry logic for transient Anthropic API errors.
