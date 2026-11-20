# Delegate to the OpenCode LXC

Pick the LXC agent by whether the run may change things (nyanshiba's guidance, 2026-11-12).

- For a research-only task （調査タスク, no implementation), use the plan-restricted agent on the LXC. It is build-auto without implementation capability, so the brief does not need defensive no-install/no-write cautions.

- Use build-auto for runs that may actually build or change things.

- Respect the operating boundary agreed with the user for the target host; stop for the user's say-so before irreversible steps or anything that transmits data externally.
