"""
Agent 1: Log Analysis Agent
Reads all three log files, uses Groq (llama-3.3-70b-versatile) to identify root cause,
extracts evidence, and produces a structured handoff for Agent 2.
"""

import os
import json
import re
from pathlib import Path
from groq import Groq

LOGS_DIR = Path(__file__).parent

LOG_FILES = {
    "app_error": LOGS_DIR / "app-error.log",
    "nginx_error": LOGS_DIR / "nginx-error.log",
    "nginx_access": LOGS_DIR / "nginx-access.log",
}


def read_logs() -> dict[str, str]:
    logs = {}
    for key, path in LOG_FILES.items():
        with open(path) as f:
            logs[key] = f.read()
    return logs


SYSTEM_PROMPT = """You are an expert SRE / platform engineer performing production incident triage.
You will be given raw log files from a production API system.
Your job is to:
1. Identify the most likely root cause of the incident.
2. Extract the strongest log evidence (exact lines or patterns) supporting your conclusion.
3. Estimate a confidence level (HIGH / MEDIUM / LOW).
4. Note any uncertainty, missing evidence, or alternative hypotheses.
5. Output ONLY a JSON object (no markdown fences, no prose outside the JSON).

JSON schema:
{
  "root_cause_summary": "<one-sentence description>",
  "root_cause_detail": "<2-4 sentence technical explanation>",
  "confidence": "HIGH|MEDIUM|LOW",
  "key_evidence": [
    {"log_file": "<filename>", "snippet": "<exact log line or pattern>", "significance": "<why this matters>"}
  ],
  "timeline": [
    {"timestamp": "<hh:mm:ss>", "event": "<what happened>"}
  ],
  "affected_endpoints": ["<endpoint>"],
  "component_at_fault": "<component name>",
  "alternative_hypotheses": ["<hypothesis>"],
  "uncertainty_notes": "<what evidence is missing or ambiguous>",
  "handoff_for_agent2": {
    "problem_type": "<e.g. db_connection_pool_exhaustion>",
    "stack": "<e.g. Python / SQLAlchemy / PostgreSQL / Gunicorn / Nginx>",
    "specific_error_codes": ["<error>"],
    "search_keywords": ["<keyword1>", "<keyword2>"]
  }
}"""


def run(verbose: bool = True) -> dict:
    logs = read_logs()

    combined = f"""=== APP ERROR LOG (app-error.log) ===
{logs['app_error']}

=== NGINX ERROR LOG (nginx-error.log) ===
{logs['nginx_error']}

=== NGINX ACCESS LOG (nginx-access.log) ===
{logs['nginx_access']}
"""

    client = Groq()  # reads GROQ_API_KEY from env

    if verbose:
        print("[Agent 1] Sending logs to Groq (llama-3.3-70b-versatile) for analysis...")

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        max_tokens=2000,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Analyze these production logs and identify the root cause:\n\n{combined}",
            },
        ],
    )

    raw = response.choices[0].message.content.strip()

    # Strip any accidental markdown fences
    raw = re.sub(r"^```json\s*", "", raw)
    raw = re.sub(r"```$", "", raw)

    result = json.loads(raw)

    if verbose:
        print("[Agent 1] Analysis complete.")
        print(f"  Root cause: {result['root_cause_summary']}")
        print(f"  Confidence: {result['confidence']}")
        print(f"  Component:  {result['component_at_fault']}")

    return result


if __name__ == "__main__":
    output = run()
    out_path = Path(__file__).parent / "agent1_output.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\n[Agent 1] Output saved to {out_path}")
