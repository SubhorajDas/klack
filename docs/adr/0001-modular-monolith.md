# ADR 0001: Begin as a modular monolith

- Status: accepted
- Date: 2026-08-13

## Context

Klack will eventually contain identity, workspaces, channels, messaging, files, documents,
boards, notifications, and search. These capabilities need clear ownership, but the product
does not yet have the scale, team boundaries, or independent reliability requirements that
justify distributed services.

## Decision

Build one feature-oriented backend codebase and one PostgreSQL database. Feature modules own
their business rules and tables. They communicate synchronously through explicit application
interfaces when one transaction must preserve an invariant, and through committed events for
asynchronous side effects.

HTTP, realtime gateways, migration jobs, and background workers may run as independently
scaled processes while importing the same application modules. A deployment-process boundary
is not treated as a service boundary.

## Consequences

- Local development and transactional consistency remain straightforward.
- Module boundaries must be protected by conventions and architecture tests.
- Cross-module SQLAlchemy object graphs and generic shared business services are prohibited.
- PostgreSQL remains the durable source of truth; queues and caches may be rebuilt.
- A module can be extracted later only when measured scaling, reliability, security, or team
  ownership needs make the operational cost worthwhile.
