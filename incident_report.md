# Incident Report — Database Connection Pool Exhaustion Remediation
**Generated:** 2026-04-26 12uman:47 UTC  
**Severity:** P1  
**Estimated Resolution:** 2-4 hours

---

## 1. Root Cause (Agent 1)

**Summary:** Database connection pool exhaustion due to a suspected session leak

The application's database connection pool is being exhausted due to a suspected session leak. The connection pool size is 20 with a maximum overflow of 5, but the application is consistently checking out more connections than are available, leading to timeouts and errors. This is likely due to the application not properly closing database sessions after use, causing the connections to remain checked out and unavailable for other requests.

**Confidence:** HIGH  
**Faulty Component:** `Database connection pool`  
**Affected Endpoints:** /api/v1/portfolio/summary, /api/v1/orders/rebalance, /api/v1/auth/login, /api/v1/recommendations, /api/v1/watchlist, /api/v1/orders

### Timeline
- `11:38:01` — Application startup
- `11:40:48` — Connection pool usage high warning
- `11:41:02` — Connection pool exhaustion error
- `11:41:17` — Suspected session leak warning

### Key Evidence
- **app-error.log**: `2026-03-17 11:40:48,062 WARN  [sqlalchemy.pool.impl.QueuePool] Connection pool usage high checked_out=18 overflow=2 pool_size=20 max_overflow=5`  
  → Early warning of connection pool usage being high
- **app-error.log**: `2026-03-17 11:41:02,902 ERROR [sqlalchemy.pool.impl.QueuePool] QueuePool limit of size 20 overflow 5 reached, connection timed out, timeout 30.00`  
  → Connection pool exhaustion error
- **app-error.log**: `2026-03-17 11:41:17,071 WARN  [api.db] suspected session leak count=23 release_rate_below_threshold=true`  
  → Suspected session leak warning

### Alternative Hypotheses
- Database server overload
- Network connectivity issues

---

## 2. Solution Options (Agent 2)

**Research note:** S5 (rollback) stops the bleeding fastest. S1 (code fix) is the permanent cure. S2 and S4 are mitigations that should follow S1 if pool settings need tuning post-fix. S3 is a circuit-breaker of last resort.

**Recommended order:** S5 → S1 → S2 → S3 → S4

### S1: Fix session leak in rebalance_service.py (immediate code fix)
**Risk:** LOW — pure code change, no infra touch  ✅ Production-safe

The logs show a session close was skipped at portfolio/rebalance_service.py:118 and that db connections are not returning to the pool after rebalance requests. Wrap every SessionLocal() usage in a `with` block (context manager) so the session is guaranteed to close on exit or exception.

**Pros:** Fixes the actual root cause, Low risk, Immediately stops leak
**Cons:** Requires code deploy, May not release existing leaked connections

### S2: Increase SQLAlchemy pool_size and max_overflow (short-term relief)
**Risk:** MEDIUM — higher pool size increases load on Postgres; verify max_connections headroom first  ✅ Production-safe

Raise pool_size from 20 to ~40 and max_overflow from 5 to 10 in SQLAlchemy config. This buys headroom while the code fix is prepared but does NOT fix the leak.

**Pros:** No code deploy required (config only), Immediate relief
**Cons:** Masks the leak rather than fixing it, Postgres max_connections may be a hard ceiling, Will exhaust again if leak rate is high

### S3: Temporarily disable the rebalance endpoint / rate-limit it
**Risk:** LOW infra risk, HIGH business impact — users cannot rebalance  ✅ Production-safe

Return 503 or queue rebalance requests while the fix is deployed. This stops new connections from being leaked while the root cause is addressed.

**Pros:** Stops the leak source immediately, Buys time for careful fix
**Cons:** Degrades service for rebalance users, Requires fast communication

### S4: Increase Postgres max_connections and tune superuser reservation
**Risk:** MEDIUM — each Postgres connection uses ~5-10 MB RAM; validate server memory first  ✅ Production-safe

Postgres rejected connections with 'remaining connection slots are reserved for superuser'. Raising max_connections and reducing superuser_reserved_connections gives the app more slots, but only treats the symptom.

**Pros:** Prevents Postgres-side rejection, No app code change
**Cons:** Does not fix the leak, Memory risk if connections accumulate indefinitely, Requires Postgres reload (brief disruption possible)

### S5: Rollback deployment 2026.03.17-2
**Risk:** LOW — reverts to known-good state  ✅ Production-safe

Logs explicitly note that deployment 2026.03.17-2 (deployed at 11:34) touched the db session lifecycle in the rebalance workflow, and the incident started at 11:40. Rolling back to 2026.03.17-1 would immediately stop new leaks.

**Pros:** Fastest way to stop the leak, No new code required, Strong causal evidence in logs supports this
**Cons:** Loses any other fixes in 2026.03.17-2, Need to identify what changed in that deploy

### Sources Retrieved
- [SQLAlchemy Connection Pooling — official docs](https://docs.sqlalchemy.org/en/20/core/pooling.html) ✓
- [PostgreSQL — Connection Settings (max_connections)](https://www.postgresql.org/docs/current/runtime-config-connection.html) ✓
- [Gunicorn — Configuration (workers, timeout)](https://docs.gunicorn.org/en/stable/settings.html) ✗ (unavailable)
- [SQLAlchemy ORM — Session Lifecycle](https://docs.sqlalchemy.org/en/20/orm/session_basics.html) ✓
- [Gunicorn FAQ — Worker Timeouts](https://docs.gunicorn.org/en/stable/faq.html) ✗ (unavailable)
- [Nginx — proxy_read_timeout / proxy_connect_timeout](https://nginx.org/en/docs/http/ngx_http_proxy_module.html) ✓

---

## 3. Remediation Runbook (Agent 3)

**Chosen solution:** S5: Rollback deployment 2026.03.17-2 and S1: Fix session leak in rebalance_service.py

**Rationale:** Rolling back to a known-good state (S5) immediately stops new leaks, and then applying the code fix (S1) addresses the root cause. This approach minimizes risk and downtime while ensuring a permanent resolution.

### Pre-Checks
1. **Verify current deployment version and identify previous artifact (2026.03.17-1) for rollback**
   → Expected: Confirmation of current deployment version and identification of previous artifact
2. **Check Postgres max_connections and available connection slots**
   → Expected: Knowledge of current Postgres connection limits and available headroom
3. **Review recent changes to the rebalance workflow and session lifecycle in the rebalance_service.py file**
   → Expected: Understanding of recent code changes and potential causes of the session leak

### Remediation Steps
**Step 1:** Trigger rollback to deployment 2026.03.17-1 via CI/CD or manually swap the application bundle
- Notes: This step reverts the application to a known-good state, stopping new leaks
- Validate: Verify that the rollback is successful and the application is running with the previous version

**Step 2:** Restart gunicorn workers: `systemctl restart gunicorn` or equivalent
- Notes: This step ensures that the rolled-back application is fully restarted
- Validate: Confirm that gunicorn workers are restarted and the application is responding

**Step 3:** Locate portfolio/rebalance_service.py line 118 and replace bare `session = SessionLocal()` with `with SessionLocal() as session:`
- Notes: This step applies the code fix to address the session leak
- Validate: Verify that the code change is correctly applied and the session leak is fixed

**Step 4:** Deploy the code fix (deployment version 2026.03.17-3 or hotfix branch)
- Notes: This step ensures that the code fix is deployed to the production environment
- Validate: Confirm that the deployment is successful and the application is running with the updated code

### Post-Fix Validation
- **Connection pool metrics**
  - How: `Monitor pool metrics using a tool like Prometheus or Grafana`
  - Pass: Confirmed stabilization of checked_out connections and no further errors
- **Application logs**
  - How: `Review application logs for any errors or warnings related to database connections`
  - Pass: No errors or warnings related to database connections
- **Postgres connection slots**
  - How: `Verify available Postgres connection slots using `SHOW max_connections;` in psql`
  - Pass: Sufficient available connection slots

### Rollback Plan
**Trigger:** If the remediation steps cause further issues or do not resolve the connection pool exhaustion
- Revert the code change and redeploy the previous version
- Revert any changes made to Postgres configuration

### Parallel Mitigations
- Increase Postgres max_connections and tune superuser reservation (S4) to prevent Postgres-side rejection
- Temporarily disable the rebalance endpoint or rate-limit it (S3) to stop new connections from being leaked

### Escalation
If the remediation steps do not resolve the issue within 2 hours, escalate to the on-call DBA and/or the development team lead

### Stakeholder Update
> Database connection pool exhaustion incident: current status is [insert current status], expected resolution time is [insert expected resolution time]

### Long-term Recommendations
- Regularly review and monitor database connection pool metrics
- Implement automated monitoring and alerting for database connection pool issues
- Consider increasing the Postgres max_connections limit and tuning superuser reservation

---
_Report generated by 3-agent incident-response system._