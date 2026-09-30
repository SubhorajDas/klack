# Klack documentation

Start with the [project README](../README.md) to run the full application.

| Task | Read |
| --- | --- |
| Set up development or troubleshoot local access | [Development guide](development.md) |
| Understand endpoints and browser security | [API guide](api.md) and the running API's `/openapi.json` |
| Understand processes, data ownership, and consistency | [Architecture overview](architecture/README.md) |
| Configure deployments, health checks, and workers | [Operations guide](operations.md) |
| Change backend behavior | [Backend guide](../backend/README.md) |
| Change UI behavior or run browser tests | [Frontend guide](../frontend/README.md) |
| Understand why a design was chosen | [Architecture decision records](adr/README.md) |
| Exercise concurrent HTTP traffic | [Performance guide](../performance/README.md) |

The architecture overview describes the implemented system. ADRs preserve the context and
tradeoffs of individual decisions; planned capabilities mentioned in them are not necessarily
implemented. Package manifests, configuration validation, migrations, and route definitions
remain the source of truth when changing these guides.

- [File sharing](files.md): upload lifecycle, deployment, limits, and cleanup.
