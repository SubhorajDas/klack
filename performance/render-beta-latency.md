# Render beta latency improvement

Recorded on 2 October 2026 (Asia/Kolkata). Implementation commit:
`f7c12b0` (`Reduce authentication round trips and deployment connection contention`).

## Problem and deployment context

The deployed app worked, but navigation felt slow during normal use, including after
the service was awake. Next.js, FastAPI, and the maintenance workers share one free
Render Docker service in Singapore. PostgreSQL uses Supabase's Session pooler in
`ap-south-1` (Mumbai). Database requests cross the network between these regions.

The first supplied production log showed call inbox checks approximately every three
seconds, usually taking about 500 ms. During a burst of requests, read-cursor and alert
responses reached about 1.7 seconds. These are backend `http_request_completed`
durations, not browser rendering measurements.

Two avoidable costs were identified in the implementation: authenticated requests
loaded the session and user separately, and the API's pool allowed only two concurrent
database connections. Concurrent channel, membership, unread-state, alert, and call
requests could therefore compete for the same two connections. The logs did not
measure connection wait time, so pool contention was a plausible contributor rather
than a proven explanation for every slow request.

## Changes and reasoning

| Change | Why it was made | Behavior retained |
| --- | --- | --- |
| Join the session and user lookup in `get_active_session` | Reduce the authentication lookup from two SQL statements to one, saving a network round trip on authenticated requests | Session ownership, expiry, revocation, and disabled-user checks remain enforced against the database |
| Increase `DB_POOL_SIZE` from `2` to `5`; keep `DB_MAX_OVERFLOW=0` | Let more simultaneous requests use the database without immediately waiting behind each other, while keeping connections bounded | Connection timeouts, recycling, and health checks remain enabled |
| Stop call inbox polling when `/calls` returns `enabled=false` | Avoid recurring authenticated requests for an unavailable feature | Enabled voice calls retain their existing polling and heartbeat behavior |

The pool setting is per process. API, identity worker, and file cleanup worker have
separate pools; increasing the setting also increases their potential connection
allowance. Connections are opened as needed. This is a small-demo configuration,
not an instruction to increase connection counts without checking provider limits.

Voice calling was configured in the prepared production environment, so the disabled
polling change does not explain the observed improvement while calls are enabled.
Browser data caching was discussed afterward but **was not implemented in this change**.
No authorization results were cached or security checks bypassed.

## Production observations

The user reported that the app felt faster after the change. The two supplied log
excerpts cover these UTC windows:

- Before: 1 October 2026, approximately 18:44:20–18:46:37 UTC.
- After: 1 October 2026, approximately 19:06:59–19:07:38 UTC.

Both windows fall on 2 October in Asia/Kolkata. Statistics below use all entries for
each route in the respective excerpt. Counts differ and the later sample includes
call actions and connection-token requests, so the workloads are not identical.

| GET route | Before count | Before median (ms) | Before range (ms) | After count | After median (ms) | After range (ms) |
| --- | ---: | ---: | --- | ---: | ---: | --- |
| `/calls` | 46 | 509.6 | 494.7–1104.4 | 24 | 448.3 | 436.3–2331.2 |
| `/workspaces/{workspace_id}/channels/{channel_id}/read-cursor` | 2 | 1307.3 | 895.3–1719.3 | 10 | 830.5 | 761.3–844.6 |
| `/workspaces/{workspace_id}/alerts` | 1 | 1719.9 | 1719.9 | 1 | 683.0 | 683.0 |
| `/workspaces/{workspace_id}/memberships` | 1 | 620.7 | 620.7 | 5 | 575.3 | 563.5–581.2 |

The call-check median was approximately 12% lower in the later excerpt. Read-cursor
requests also had lower observed durations, consistent with the reported improvement
in navigation. The single alert observation in each window cannot establish typical
alert latency. These samples are observational evidence, not a controlled benchmark
or a production capacity estimate; they do not isolate the effect of each change.

Significant delays remain. The later excerpt includes call checks up to 2331.2 ms,
call-control POST requests between 1724.0 and 2415.1 ms, and connection-token POST
requests between 1663.7 and 2902.5 ms. The 17 call checks after 19:07:15 UTC settle into
a 436.3–453.7 ms range, but that narrower window must not hide the earlier spikes.
The logs alone do not establish the cause of those spikes. All requests in both
provided excerpts returned HTTP 200.

## Validation and rollout

- 56 selected authentication repository/service, API, and realtime tests passed.
- Browser tests for outgoing calls, incoming calls, and microphone denial passed.
- The new disabled-voice test confirmed polling stops after initial requests settle;
  it accommodates development Strict Mode's initial remount.
- The opt-in LiveKit media smoke test was skipped; these checks do not establish
  end-to-end audio performance.
- The production frontend build, Python linting, and repository type check passed.

The code was pushed to `main` at commit `f7c12b0`. For a manually created Render web
service, set `DB_POOL_SIZE=5` and `DB_MAX_OVERFLOW=0` in **Environment**, then save and
deploy. Updating `render.yaml` alone does not update that service's environment.
The local ignored `.env.render` was also updated for future imports. Operational
instructions are in [the Render deployment guide](../docs/render.md).

For further investigation, measure database statement time and connection-pool wait
time separately, then compare the same navigation and call journeys under the same
load. A future browser cache could show previously loaded conversations immediately
and refresh them in the background, with realtime updates and access invalidation;
that work is separate from this optimization.

## Implementation references

- [Authentication repository](../backend/src/klack/modules/identity/infrastructure/repository.py)
- [Database pool construction](../backend/src/klack/core/db/session.py)
- [Call polling](../frontend/src/components/calls.tsx)
- [Call browser tests](../frontend/tests/calls.spec.ts)
- [Render defaults](../render.yaml)

Only aggregate timings are recorded here. Raw log excerpts, request identifiers,
credentials, cookies, and private application content are not copied into this report.
