# Incident Response System — 3-Agent Pipeline

A working multi-agent system that ingests production logs, diagnoses the root cause, researches solutions from authoritative sources, and produces a step-by-step remediation runbook.

> **Note on LLM choice:** The brief suggests Gemini (`gemini-2.5-flash`). This implementation uses Groq (`llama-3.3-70b-versatile`) which is free, requires no billing setup, and produces equivalent structured JSON output. Switching to Gemini requires only swapping the client initialisation in `agents/agent1_log_analysis.py` and `agents/agent3_resolution_planner.py`.

---

## Project Structure

```
PhoneMe-Assignment/
├── main.py                              # Orchestrator — run this
├── requirements.txt
├── .env.example
├── agents/
│   ├── agent1_log_analysis.py           # Log Analysis Agent      (Groq LLM)
│   ├── agent2_solution_research.py      # Solution Research Agent  (HTTP scraping, no LLM)
│   └── agent3_resolution_planner.py     # Resolution Planner Agent (Groq LLM)
├── logs/
│   ├── app-error.log
│   ├── nginx-error.log
│   └── nginx-access.log
└── outputs/                             # Auto-created on first run
    ├── agent1_output.json               # ← sample execution result included
    ├── agent2_output.json               # ← sample execution result included
    ├── agent3_output.json               # ← sample execution result included
    └── incident_report.md               # ← sample execution result (human-readable)
```

> **Sample execution results** for the provided logs are already committed in the `outputs/` folder. `outputs/incident_report.md` is the human-readable final report. The three JSON files show each agent's raw structured output.

---

## Quick Start

### 1. Prerequisites

- Python 3.11+
- A Groq API key — get one free at https://console.groq.com (no billing required)

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Set your API key

```bash
export GROQ_API_KEY="gsk_..."
```

### 4. Run

```bash
python main.py
```

Optional flags:
```bash
python main.py --quiet    # suppress per-agent progress output
```

### 5. Outputs

After a successful run, `outputs/` will contain:

| File | Contents |
|------|----------|
| `agent1_output.json` | Root cause analysis + structured handoff |
| `agent2_output.json` | Solution options + source excerpts + URLs |
| `agent3_output.json` | Remediation runbook (structured JSON) |
| `incident_report.md` | Human-readable full incident report |

Sample execution results for the provided logs are already present in `outputs/` — you can review them without running the system.

---

## Architecture

### Agent Boundaries

| Agent | Responsibility | LLM? |
|-------|---------------|------|
| **Agent 1 — Log Analysis** | Reads all 3 log files; uses Groq (`llama-3.3-70b-versatile`) to identify root cause, extract evidence, and assign confidence. Prompts are visible in `agents/agent1_log_analysis.py` | ✅ Groq LLM |
| **Agent 2 — Solution Research** | Fetches authoritative docs via HTTP (SQLAlchemy, PostgreSQL, Gunicorn, Nginx official docs); extracts relevant excerpts; builds structured solution options using heuristics — **no LLM used** | ❌ HTTP scraping only |
| **Agent 3 — Resolution Planner** | Receives Agent 1 + Agent 2 outputs; uses Groq (`llama-3.3-70b-versatile`) to select the safest solution and write an operator-ready runbook with pre/post checks. Prompts are visible in `agents/agent3_resolution_planner.py` | ✅ Groq LLM |

### Handoff Format

Each agent produces a JSON object. The pipeline passes these directly as Python dicts (in-process) and also persists them to `outputs/agentN_output.json` for inspection. Any agent can be re-run in isolation by loading its input file from `outputs/`.

Key handoff fields:

- **Agent 1 → Agent 2:** `handoff_for_agent2` containing `problem_type`, `stack`, `specific_error_codes`, `search_keywords` — used by Agent 2 to select relevant documentation sources.
- **Agent 2 → Agent 3:** `handoff_for_agent3` containing `top_solution_ids`, `confirmed_root_cause`, `deploy_version_implicated`, `code_path` — used by Agent 3 to focus its planning.

### Why These Implementation Choices Are Production-Reasonable

1. **LLM for analysis and planning, not research:** LLMs excel at synthesising unstructured text (logs) and generating structured instructions. They are unreliable at citing current, versioned documentation. Agent 2 therefore uses real HTTP retrieval from official sources — SQLAlchemy docs, PostgreSQL runtime config, Gunicorn settings, Nginx proxy module — and flags which sources were successfully retrieved.

2. **Structured JSON handoffs:** Each agent outputs validated JSON with a known schema. This makes the pipeline debuggable — you can inspect or re-run any single agent from its persisted input file without re-running the whole pipeline.

3. **Curated source registry in Agent 2:** Rather than a general web search (which risks SEO-spam results), Agent 2 uses a curated registry of known-reliable technical sources keyed to the problem type returned by Agent 1.

4. **Risk flags built in:** Agent 2 explicitly outputs a `risky_actions_avoid` list (e.g. `pg_terminate_backend`, `pool_timeout=0`). Agent 3's system prompt instructs it to prioritise safety and include a rollback plan.

5. **No secrets in code:** The Groq API key is read exclusively from the `GROQ_API_KEY` environment variable. No key is hardcoded anywhere in the repository.

---

## What the System Concluded for This Incident

The system identified a **P1 database connection pool exhaustion** incident caused by a **session leak introduced in deployment `2026.03.17-2`**.

Specifically:
- `portfolio/rebalance_service.py:118` was modified in the 11:34 deploy and does not close its SQLAlchemy session on error paths.
- Starting at ~11:40, connections stopped returning to the pool.
- At 11:41, the pool was fully exhausted (25 checked out, 0 idle, 41+ waiters).
- All DB-dependent endpoints began returning 504/502 errors.
- Two Gunicorn workers timed out and were recycled.

**Recommended fix:** Roll back `2026.03.17-2` immediately (stops the leak in ~2 minutes), then apply a permanent code fix wrapping `SessionLocal()` in a context manager at `rebalance_service.py:118`.

See `outputs/incident_report.md` for the full runbook including pre-checks, remediation steps, post-fix validation, and rollback plan.

---

## Limitations, Assumptions & Missing Production Safeguards

### Limitations
- **Internet access required for Agent 2:** If the environment is air-gapped, source excerpts will show `(unavailable)` but the solution list is still populated from engineering heuristics encoded in the agent.
- **Static log snapshot:** The pipeline processes logs as a static file snapshot. It does not tail live streams or re-trigger on new log entries.
- **Single LLM provider, no fallback:** Both Agent 1 and Agent 3 depend on the Groq API. A Groq outage halts the pipeline with no retry or fallback provider.
- **Gunicorn-specific runbook:** Agent 3's steps assume systemd-managed Gunicorn (`systemctl restart gunicorn`). Docker, Kubernetes, or supervisord require different commands.

### Assumptions
- The three log files cover the same incident window from a single-node deployment.
- Deployment version strings in `app-error.log` (e.g. `2026.03.17-2`) map to real CI/CD artifacts.
- `portfolio/rebalance_service.py:118` is the only unclosed session site — no full audit was performed on other call sites.

### Missing Production Safeguards
- **No retry logic** on Groq API calls or HTTP doc fetches — transient failures cause total pipeline failure.
- **No schema validation** on LLM output — agent outputs are parsed directly with `json.loads()`. A Pydantic model should gate each handoff.
- **No secrets manager** — `GROQ_API_KEY` is a plain environment variable. Production should use Vault, AWS Secrets Manager, or equivalent.
- **No log sanitisation** — logs are sent verbatim to the Groq API. PII, session tokens, or internal IPs in logs are transmitted to a third-party LLM.
- **No observability** — the pipeline emits only stdout. No structured logging, metrics, or tracing.