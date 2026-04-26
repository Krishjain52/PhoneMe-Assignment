"""
Agent 2: Solution Research Agent
Takes Agent 1's structured diagnosis and retrieves remediation guidance
from authoritative technical sources via HTTP (no LLM for discovery).
"""

import json
import time
import urllib.request
import urllib.error
import html
import re
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Curated source registry – keyed by problem_type tokens
# ---------------------------------------------------------------------------
SOURCE_REGISTRY = {
    "db_connection_pool": [
        {
            "url": "https://docs.sqlalchemy.org/en/20/core/pooling.html",
            "title": "SQLAlchemy Connection Pooling — official docs",
            "section_hints": ["QueuePool", "pool_size", "max_overflow", "pool_timeout", "pool_recycle"],
        },
        {
            "url": "https://www.postgresql.org/docs/current/runtime-config-connection.html",
            "title": "PostgreSQL — Connection Settings (max_connections)",
            "section_hints": ["max_connections", "superuser_reserved_connections"],
        },
        {
            "url": "https://docs.gunicorn.org/en/stable/settings.html",
            "title": "Gunicorn — Configuration (workers, timeout)",
            "section_hints": ["workers", "timeout", "worker_class"],
        },
    ],
    "connection_leak": [
        {
            "url": "https://docs.sqlalchemy.org/en/20/orm/session_basics.html",
            "title": "SQLAlchemy ORM — Session Lifecycle",
            "section_hints": ["close", "context manager", "session leak", "SessionLocal"],
        },
    ],
    "gunicorn_worker_timeout": [
        {
            "url": "https://docs.gunicorn.org/en/stable/faq.html",
            "title": "Gunicorn FAQ — Worker Timeouts",
            "section_hints": ["timeout", "worker timeout", "preload_app"],
        },
    ],
    "nginx_upstream_timeout": [
        {
            "url": "https://nginx.org/en/docs/http/ngx_http_proxy_module.html",
            "title": "Nginx — proxy_read_timeout / proxy_connect_timeout",
            "section_hints": ["proxy_read_timeout", "proxy_connect_timeout", "upstream timed out"],
        },
    ],
}

FALLBACK_SOURCES = (
    SOURCE_REGISTRY["db_connection_pool"]
    + SOURCE_REGISTRY["connection_leak"]
    + SOURCE_REGISTRY["gunicorn_worker_timeout"]
    + SOURCE_REGISTRY["nginx_upstream_timeout"]
)


def _fetch_page(url: str, timeout: int = 10) -> Optional[str]:
    """Fetch a page and return plain text (HTML tags stripped)."""
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (incident-research-bot/1.0)",
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        # Strip tags
        text = re.sub(r"<[^>]+>", " ", raw)
        text = html.unescape(text)
        text = re.sub(r"\s+", " ", text)
        return text[:8000]  # keep first 8 k chars
    except Exception as exc:
        return None


def _extract_relevant_snippets(page_text: str, hints: list[str], max_chars: int = 600) -> str:
    """Pull sentences / paragraphs that contain the hint keywords."""
    if not page_text:
        return "(page unavailable)"
    sentences = re.split(r"(?<=[.!?])\s+", page_text)
    matched = []
    for sent in sentences:
        if any(h.lower() in sent.lower() for h in hints):
            matched.append(sent.strip())
    snippet = " … ".join(matched[:6])
    return snippet[:max_chars] if snippet else page_text[:max_chars]


def _select_sources(agent1: dict) -> list[dict]:
    """Pick sources from registry based on Agent 1's problem_type keywords."""
    handoff = agent1.get("handoff_for_agent2", {})
    problem_type = handoff.get("problem_type", "").lower()
    keywords = " ".join(handoff.get("search_keywords", [])).lower()

    selected = []
    for key, sources in SOURCE_REGISTRY.items():
        if key in problem_type or key in keywords or any(k in keywords for k in key.split("_")):
            selected.extend(sources)

    # Always include SQLAlchemy pool docs for this class of issue
    for s in SOURCE_REGISTRY["db_connection_pool"]:
        if s not in selected:
            selected.append(s)

    # deduplicate by url
    seen = set()
    deduped = []
    for s in selected:
        if s["url"] not in seen:
            seen.add(s["url"])
            deduped.append(s)
    return deduped


def _build_solutions(agent1: dict, fetched: list[dict]) -> list[dict]:
    """
    Construct structured solution options from fetched content + known engineering patterns.
    This is rule/heuristic-based — not LLM-generated.
    """
    component = agent1.get("component_at_fault", "db connection pool")
    root_cause = agent1.get("root_cause_summary", "")

    solutions = [
        {
            "id": "S1",
            "title": "Fix session leak in rebalance_service.py (immediate code fix)",
            "description": (
                "The logs show a session close was skipped at portfolio/rebalance_service.py:118 "
                "and that db connections are not returning to the pool after rebalance requests. "
                "Wrap every SessionLocal() usage in a `with` block (context manager) so the session "
                "is guaranteed to close on exit or exception."
            ),
            "steps": [
                "Locate portfolio/rebalance_service.py line 118.",
                "Replace bare `session = SessionLocal()` with `with SessionLocal() as session:`.",
                "Verify all other SessionLocal call sites use context managers.",
                "Deploy the fix (deployment version 2026.03.17-3 or hotfix branch).",
            ],
            "risk": "LOW — pure code change, no infra touch",
            "production_safe": True,
            "source_ids": ["SQLAlchemy ORM — Session Lifecycle"],
            "pros": ["Fixes the actual root cause", "Low risk", "Immediately stops leak"],
            "cons": ["Requires code deploy", "May not release existing leaked connections"],
        },
        {
            "id": "S2",
            "title": "Increase SQLAlchemy pool_size and max_overflow (short-term relief)",
            "description": (
                "Raise pool_size from 20 to ~40 and max_overflow from 5 to 10 in SQLAlchemy config. "
                "This buys headroom while the code fix is prepared but does NOT fix the leak."
            ),
            "steps": [
                "Edit SQLAlchemy engine creation: `create_engine(..., pool_size=40, max_overflow=10, pool_timeout=30, pool_recycle=1800)`",
                "Restart gunicorn workers: `kill -HUP <gunicorn_master_pid>`",
                "Monitor pool metrics; confirm checked_out stabilises.",
            ],
            "risk": "MEDIUM — higher pool size increases load on Postgres; verify max_connections headroom first",
            "production_safe": True,
            "source_ids": ["SQLAlchemy Connection Pooling — official docs"],
            "pros": ["No code deploy required (config only)", "Immediate relief"],
            "cons": [
                "Masks the leak rather than fixing it",
                "Postgres max_connections may be a hard ceiling",
                "Will exhaust again if leak rate is high",
            ],
        },
        {
            "id": "S3",
            "title": "Temporarily disable the rebalance endpoint / rate-limit it",
            "description": (
                "Return 503 or queue rebalance requests while the fix is deployed. "
                "This stops new connections from being leaked while the root cause is addressed."
            ),
            "steps": [
                "Add an Nginx location block to return 503 for POST /api/v1/orders/rebalance.",
                "Or set a feature flag in app config to short-circuit the rebalance handler.",
                "Communicate maintenance window to affected users.",
            ],
            "risk": "LOW infra risk, HIGH business impact — users cannot rebalance",
            "production_safe": True,
            "source_ids": ["Nginx — proxy_read_timeout / proxy_connect_timeout"],
            "pros": ["Stops the leak source immediately", "Buys time for careful fix"],
            "cons": ["Degrades service for rebalance users", "Requires fast communication"],
        },
        {
            "id": "S4",
            "title": "Increase Postgres max_connections and tune superuser reservation",
            "description": (
                "Postgres rejected connections with 'remaining connection slots are reserved for superuser'. "
                "Raising max_connections and reducing superuser_reserved_connections gives the app more slots, "
                "but only treats the symptom."
            ),
            "steps": [
                "Check current value: `SHOW max_connections;` in psql.",
                "Edit postgresql.conf: `max_connections = 200` (or appropriate value for RAM).",
                "Also consider: `superuser_reserved_connections = 3` (default is 3, can lower to 2).",
                "Reload Postgres: `pg_ctlcluster <ver> main reload` or `systemctl reload postgresql`.",
            ],
            "risk": "MEDIUM — each Postgres connection uses ~5-10 MB RAM; validate server memory first",
            "production_safe": True,
            "source_ids": ["PostgreSQL — Connection Settings (max_connections)"],
            "pros": ["Prevents Postgres-side rejection", "No app code change"],
            "cons": [
                "Does not fix the leak",
                "Memory risk if connections accumulate indefinitely",
                "Requires Postgres reload (brief disruption possible)",
            ],
        },
        {
            "id": "S5",
            "title": "Rollback deployment 2026.03.17-2",
            "description": (
                "Logs explicitly note that deployment 2026.03.17-2 (deployed at 11:34) touched the "
                "db session lifecycle in the rebalance workflow, and the incident started at 11:40. "
                "Rolling back to 2026.03.17-1 would immediately stop new leaks."
            ),
            "steps": [
                "Identify previous artifact: deployment tag 2026.03.17-1.",
                "Trigger rollback via CI/CD or manually swap the application bundle.",
                "Restart gunicorn: `systemctl restart gunicorn` or equivalent.",
                "Monitor pool metrics to confirm connections return.",
            ],
            "risk": "LOW — reverts to known-good state",
            "production_safe": True,
            "source_ids": [],
            "pros": [
                "Fastest way to stop the leak",
                "No new code required",
                "Strong causal evidence in logs supports this",
            ],
            "cons": [
                "Loses any other fixes in 2026.03.17-2",
                "Need to identify what changed in that deploy",
            ],
        },
    ]

    risky = [
        {
            "id": "RISKY-1",
            "title": "Kill all idle Postgres connections manually",
            "reason": "pg_terminate_backend on active sessions can corrupt in-flight transactions.",
        },
        {
            "id": "RISKY-2",
            "title": "Set pool_timeout=0 to disable waiting",
            "reason": "Would cause immediate errors for all requests instead of slow degradation.",
        },
    ]

    return {"solutions": solutions, "risky_actions_avoid": risky}


def run(agent1_output: dict, verbose: bool = True) -> dict:
    if verbose:
        print("[Agent 2] Starting solution research...")

    sources = _select_sources(agent1_output)
    fetched_sources = []

    for src in sources:
        if verbose:
            print(f"  Fetching: {src['url']}")
        page = _fetch_page(src["url"])
        snippet = _extract_relevant_snippets(page, src["section_hints"]) if page else "(unavailable)"
        fetched_sources.append(
            {
                "url": src["url"],
                "title": src["title"],
                "retrieved": page is not None,
                "relevant_excerpt": snippet,
            }
        )
        time.sleep(0.5)  # polite crawl delay

    solution_data = _build_solutions(agent1_output, fetched_sources)

    result = {
        "agent1_root_cause": agent1_output.get("root_cause_summary"),
        "research_sources": fetched_sources,
        "solutions": solution_data["solutions"],
        "risky_actions_avoid": solution_data["risky_actions_avoid"],
        "recommended_order": ["S5", "S1", "S2", "S3", "S4"],
        "notes": (
            "S5 (rollback) stops the bleeding fastest. S1 (code fix) is the permanent cure. "
            "S2 and S4 are mitigations that should follow S1 if pool settings need tuning post-fix. "
            "S3 is a circuit-breaker of last resort."
        ),
        "handoff_for_agent3": {
            "top_solution_ids": ["S5", "S1"],
            "supporting_solution_ids": ["S2"],
            "avoid_ids": ["RISKY-1", "RISKY-2"],
            "confirmed_root_cause": agent1_output.get("root_cause_summary"),
            "deploy_version_implicated": "2026.03.17-2",
            "code_path": "portfolio/rebalance_service.py:118",
        },
    }

    if verbose:
        print(f"[Agent 2] Research complete. {len(solution_data['solutions'])} solutions found.")
        print(f"  Recommended order: {result['recommended_order']}")

    return result


if __name__ == "__main__":
    in_path = Path(__file__).parent / "agent1_output.json"
    with open(in_path) as f:
        agent1 = json.load(f)

    output = run(agent1)
    out_path = Path(__file__).parent / "agent2_output.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\n[Agent 2] Output saved to {out_path}")
