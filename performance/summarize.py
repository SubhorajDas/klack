"""Read k6 JSON output without collecting request/response bodies or secrets."""

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import json
from pathlib import Path


def percentile(values, fraction):
    if not values:
        return 0
    values = sorted(values)
    position = (len(values) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def read_points(path):
    durations = defaultdict(list)
    minutes = defaultdict(list)
    journeys = Counter()
    failed = Counter()
    statuses = Counter()
    accepted = refreshes = total = 0
    first = last = None
    request_times = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue  # A live writer may not have finished its last line.
            if item.get("type") != "Point":
                continue
            data = item["data"]
            tags = data.get("tags", {})
            metric = item["metric"]
            if metric == "journeys":
                journeys[tags["journey"]] += data["value"]
            elif metric == "journey_success" and data["value"] == 0:
                failed[tags["journey"]] += 1
            elif metric == "invitations_accepted":
                accepted += data["value"]
            elif metric == "http_req_duration" and tags.get("phase") == "load":
                total += 1
                durations[tags["name"]].append(data["value"])
                minutes[data["time"][:16]].append(data["value"])
                statuses[tags.get("status", "unknown")] += 1
                if tags["name"] == "auth.refresh" and tags.get("status") == "200":
                    refreshes += 1
                first = first or data["time"]
                last = data["time"]
                request_times.append(datetime.fromisoformat(data['time'].replace('Z', '+00:00')))
    all_durations = [value for values in durations.values() for value in values]
    request_times.sort()
    gaps = [{"from": a.isoformat(), "to": b.isoformat(), "seconds": round((b-a).total_seconds(), 3)} for a, b in zip(request_times, request_times[1:]) if (b-a).total_seconds() > 15]
    return {
        "requests": total, "journeys": dict(journeys), "journey_failures": dict(failed),
        "invitations_accepted": accepted, "successful_refreshes": refreshes,
        "statuses": dict(statuses), "first_request": first, "last_request": last,
        "request_gaps_over_15s": gaps,
        "p95_ms": round(percentile(all_durations, .95), 2),
        "p99_ms": round(percentile(all_durations, .99), 2),
        "endpoints": {name: {"count": len(values), "p95_ms": round(percentile(values, .95), 2), "p99_ms": round(percentile(values, .99), 2)} for name, values in sorted(durations.items())},
        "minutes": {name: {"count": len(values), "p95_ms": round(percentile(values, .95), 2)} for name, values in sorted(minutes.items())},
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("metrics", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    result = read_points(args.metrics)
    if args.output:
        args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    if args.live:
        result.pop("endpoints")
        result.pop("minutes")
    print(json.dumps(result, indent=2))
