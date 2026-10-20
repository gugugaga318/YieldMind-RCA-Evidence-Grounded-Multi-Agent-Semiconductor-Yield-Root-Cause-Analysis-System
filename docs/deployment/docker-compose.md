# Docker Compose deployment

See the current [running guide](../GETTING_STARTED.md) and [configuration reference](../configuration.md).

The topology is PostgreSQL, API, an independent worker and a static frontend. Initialization and indexing are explicit tools-profile operations.

This is a local demonstration deployment, not a production authentication or high-availability platform. The unchanged Compose file forwards DashScope credentials only; DeepSeek needs a suitable host-process environment or a local override.

The seed tool resets the demo schema. Do not run it against a database with data to retain.
