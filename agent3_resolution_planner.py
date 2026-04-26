"""
Agent 3: Resolution Planner Agent
Receives Agent 1 findings + Agent 2 solution options.
Uses Groq (llama-3.3-70b-versatile) to select the safest practical solution and
produce step-by-step operator instructions with pre/post checks.
"""

import json
import re
from pathlib import Path
from groq import Groq

SYSTEM_PROMPT = """You are a senior SRE writing a production incident remediation runbook.
You will receive:
- Agent 1's root cause analysis (structured JSON)
- Agent 2's researched solution options (structured JSON)

Your job:
1. Select the safest, most practical remediation path given the evidence.
2. Write clear, ordered operator instructions a junior SRE can follow under pressure.
3. Include explicit pre-checks, validation steps between actions, and post-fix verification.
4. Include a rollback/safety plan if the chosen fix makes things worse.
5. Output ONLY a JSON object (no markdown fences, no prose outside JSON).

JSON schema:
{
  "incident_title": "<short title>",
  "severity": "P1|P2|P3",
  "recommended_solution": "<solution id and title>",
  "rationale": "<why this solution was chosen over alternatives>",
  "pre_checks": [
    {"step": 1, "action": "<check to perform before starting>", "expected_result": "<what success looks like>"}
  ],
  "remediation_steps": [
    {"step": 1, "action": "<command or action>", "notes": "<explanation>", "validation": "<how to confirm this step worked>"}
  ],
  "post_fix_validation": [
    {"check": "<what to verify>", "command_or_method": "<how>", "pass_criteria": "<what pass looks like>"}
  ],
  "rollback_plan": {
    "trigger": "<when to invoke rollback>",
    "steps": ["<step1>", "<step2>"]
  },
  "parallel_mitigations": ["<action that can run in parallel with the main fix>"],
  "escalation": "<when and to whom to escalate if fix fails>",
  "communication_template": "<brief status update to send to stakeholders>",
  "estimated_resolution_time": "<realistic ETA>",
  "long_term_recommendations": ["<follow-up action after incident is closed>"]
}"""


def run(agent1_output: dict, agent2_output: dict, verbose: bool = True) -> dict:
    if verbose:
        print("[Agent 3] Building remediation plan...")

    client = Groq()  # reads GROQ_API_KEY from env

    prompt = f"""Agent 1 — Root Cause Analysis:
{json.dumps(agent1_output, indent=2)}

Agent 2 — Solution Research:
{json.dumps(agent2_output, indent=2)}

Based on the above, produce a complete incident remediation runbook."""

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        max_tokens=3000,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    )

    raw = response.choices[0].message.content.strip()
    raw = re.sub(r"^```json\s*", "", raw)
    raw = re.sub(r"```$", "", raw)

    result = json.loads(raw)

    if verbose:
        print(f"[Agent 3] Plan complete.")
        print(f"  Severity:    {result.get('severity')}")
        print(f"  Solution:    {result.get('recommended_solution')}")
        print(f"  ETA:         {result.get('estimated_resolution_time')}")

    return result


if __name__ == "__main__":
    base = Path(__file__).parent
    with open(base / "agent1_output.json") as f:
        agent1 = json.load(f)
    with open(base / "agent2_output.json") as f:
        agent2 = json.load(f)

    output = run(agent1, agent2)
    out_path = base / "agent3_output.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\n[Agent 3] Output saved to {out_path}")
