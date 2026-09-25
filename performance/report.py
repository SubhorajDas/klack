"""Build a concise Markdown report from completed k6 and database audit results."""

import argparse
import json
from pathlib import Path

from summarize import read_points

parser = argparse.ArgumentParser()
parser.add_argument("summary", type=Path)
parser.add_argument("metrics", type=Path)
parser.add_argument("audit", type=Path)
parser.add_argument("output", type=Path)
args = parser.parse_args()
summary = json.loads(args.summary.read_text(encoding="utf-8-sig"))
audit = json.loads(args.audit.read_text(encoding="utf-8-sig"))
points = read_points(args.metrics)
metrics = summary["metrics"]
latency = metrics["http_req_duration{phase:load}"]["values"]
failure = metrics["http_req_failed{phase:load}"]["values"]["rate"]
bad_thresholds = [f"{name}: {threshold}" for name, metric in metrics.items() for threshold, result in metric.get("thresholds", {}).items() if not result["ok"]]
journey_total = sum(points["journeys"].values())
lines = [
    f"# Klack performance test — {summary['run']}", "",
    f"**Result: {'ALL THRESHOLDS PASSED' if not bad_thresholds else 'THRESHOLDS FAILED'}{' — timing qualification applies' if points['request_gaps_over_15s'] else ''}**", "",
    f"Ran {summary['users']} concurrent virtual users for {summary['duration']}, including the main account and {summary['users'] - 1} newly registered test accounts. Setup ran before the timed workload. Data remains available in the application.", "",
    f"Open **K6 {summary['run']} - shared hub** to inspect chat, threads, reactions, and DMs. Project workspaces use the same run prefix.", "",
    "## Measured results", "",
    "| Metric | Result |", "|---|---:|",
    f"| Timed HTTP requests | {points['requests']:,} |",
    f"| Completed journeys | {journey_total:,} |",
    f"| HTTP failure rate | {failure:.2%} |",
    f"| Journey success rate | {metrics['journey_success']['values']['rate']:.2%} |",
    f"| Median request latency | {latency['med']:.2f} ms |",
    f"| p95 request latency | {latency['p(95)']:.2f} ms |",
    f"| p99 request latency | {latency['p(99)']:.2f} ms |",
    f"| Maximum request latency | {latency['max']:.2f} ms |",
    f"| Successful session refreshes | {points['successful_refreshes']} |",
    f"| Runtime invitations accepted | {points['invitations_accepted']} |", "",
    f"Request timestamps (UTC): {points['first_request']} through {points['last_request']}.", "",
    f"Configured load duration: {summary['duration']}. k6 reported total run duration: {summary.get('state', {}).get('testRunDurationMs', 0) / 1000:.2f} seconds. The request stream contains {sum(g['seconds'] for g in points['request_gaps_over_15s']):.2f} seconds of gaps longer than 15 seconds. The run continued beyond 20 wall-clock minutes; this was not an uninterrupted wall-clock soak.", "",
    "Thresholds: load HTTP failures <1%; request p95 <1,000 ms and p99 <2,000 ms; overall and per-journey success >99%; per-journey p95 <5,000 ms; every journey and invitation acceptance exercised.", "",
    "## Journey mix", "", "| Journey | Target weight | Completed | Observed |", "|---|---:|---:|---:|",
]
for name, weight in summary["weights"].items():
    count = points["journeys"].get(name, 0)
    lines.append(f"| {name} | {weight}% | {count:,} | {count / journey_total:.1%} |")
lines += ["", "Each identity first completed one of each journey; subsequent choices were randomized with the stated weights. Think time was 2–6 seconds. Invitation inbox reads also ran every 15 iterations.", "", "## Persisted activity", "", "| Database check | Count |", "|---|---:|"]
for name, value in audit.items():
    lines.append(f"| {name.replace('_', ' ')} | {value:,} |")
lines += ["", "Database invitation totals include 19 bootstrap invitations before the timed run. Pending invitations can be issued near the end, after a recipient's final inbox check.", "", "## Endpoint latency", "", "| Endpoint | Requests | p95 | p99 |", "|---|---:|---:|---:|"]
for name, values in points["endpoints"].items():
    lines.append(f"| {name} | {values['count']:,} | {values['p95_ms']:.2f} ms | {values['p99_ms']:.2f} ms |")
lines += ["", "## Scope and limitations", "",
    "- Target: local Next.js development API proxy on port 3000, FastAPI, and PostgreSQL in Docker Desktop. The client and services share the same machine.",
    "- This measures authenticated HTTP API journeys. It does not measure browser rendering, UI responsiveness, or WebSocket delivery, and is not a production capacity estimate.",
    "- Invitation URLs were sent in real hub DMs and redeemed under recipients' isolated cookie sessions. Bootstrap invitations were directly handed between sessions.",
    "- The local registration limit was temporarily increased to provision accounts and restored after the run. Authentication, CSRF checks, and session refresh remained enabled.",
    "- An initial smoke test exposed a test-harness invitation-tracking bug. It was fixed before a second smoke test passed and this full run began. Smoke data is retained under separate smoke prefixes and excluded here.",
    "- Existing workspace content was not changed. Passwords and cookie values are excluded from reports and committed scripts.", "",
    "## Reproduce", "", "Run `./performance/run.ps1 -ProvisionLocalAccounts` and enter credentials at its prompts. See `performance/README.md` for parameters and workload details.", "",
]
if bad_thresholds:
    lines += ["## Failed thresholds", ""] + [f"- {value}" for value in bad_thresholds]
if points['request_gaps_over_15s']:
    lines += ["## Timing qualification", "", "The request stream contains the following wall-clock gaps. These gaps qualify the sustained-load result even if all latency and error thresholds pass. A host/Docker pause is suspected; the exact cause was not established.", ""]
    lines += [f"- {gap['from']} to {gap['to']}: {gap['seconds']:.2f} seconds without a completed request." for gap in points['request_gaps_over_15s']]
args.output.write_text("\n".join(lines), encoding="utf-8")
print(f"Report written: {args.output}")
