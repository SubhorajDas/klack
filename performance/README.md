# Weighted collaboration load test

For the deployed demo's authentication and connection-pool optimization, see
[Render beta latency improvement](render-beta-latency.md). That report uses small
production log samples and is separate from the local load test below.

Run from PowerShell with Docker Desktop and the local application running:

```powershell
./performance/run.ps1 -ProvisionLocalAccounts
```

The runner prompts for the main account email and password. Credentials are passed through process environment variables, never embedded in the script or summary. The development registration limit is temporarily raised from the configured default to 100, then the Compose configuration is restored in `finally`. Do not run simultaneous provisioning jobs; existing IP throttle blocks may need to expire. The script creates fresh accounts for every run and deliberately retains generated activity.

Defaults: 20 concurrent users (the main account plus 19 synthetic accounts), 20 minutes of constant load, 2–6 seconds of think time per journey. Setup runs before the timed workload. An eight-journey initial sequence ensures each user exercises every action, followed by independent weighted random selection:

| Journey | Weight |
|---|---:|
| Browse history/workspaces/channels and advance read cursor | 30% |
| Send channel message | 25% |
| Open DM and send/read messages | 15% |
| Quote a message and read its context | 12% |
| React to a message | 10% |
| Create channel | 3% |
| Create workspace | 2% |
| Share/redeem invitations | 3% |

All users initially join a shared test hub. Each owns new project workspaces; non-main owners invite the main account to every project. Invitations are shared as actual messages in hub DMs, then read and redeemed using the recipient's session. Bootstrap hub invitations are transferred directly between authenticated sessions. Users check their DM invitation inbox every 15 iterations and during invitation journeys. Invitations issued near the end can remain pending. Links are single-use bearer credentials; keep the test workspaces private to intended participants.

Cookie jars are isolated by user, preserve the API's cookie paths, and supply Origin and CSRF headers. Sessions refresh after ten minutes. Main-account activity is restricted to newly created test workspaces. Existing workspaces are not changed. Test identities use reserved `loadtest.example` addresses; SMTP is not involved.

The default target is the frontend development server at port 3000 through Docker's host gateway, exercising its API forwarding and the backend/database. This is an HTTP API workload: it does not measure page rendering, browser JavaScript, or WebSocket delivery. Local development measurements are not production capacity estimates.

Results are written to ignored `performance/results/`. Thresholds require load-phase HTTP failures below 1%, p95 below 1 second, p99 below 2 seconds, journey success above 99%, and each journey type and invitation acceptance to occur. Each journey also has a 5-second p95 threshold. Threshold failures return a nonzero exit code. No response bodies, passwords, cookies, invitation tokens, or user emails are written to reports. `phase:load` separates measured traffic from setup, and request names avoid UUID metric cardinality.

A short smoke test:

```powershell
./performance/run.ps1 -Duration 45s -ProvisionLocalAccounts
```

Implementation references: [k6 cookie jars](https://grafana.com/docs/k6/latest/javascript-api/k6-http/cookiejar/), [options](https://grafana.com/docs/k6/latest/using-k6/k6-options/reference/), and [custom summaries](https://grafana.com/docs/k6/latest/results-output/end-of-test/custom-summary/).
