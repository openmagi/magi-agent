# Open Magi Agent Docs

Open Magi Agent, usually shortened to Magi Agent, is the programmable AI agent
that gets real work done under your rules. These docs are the public source of
truth for installing, configuring, extending, and self-hosting the OSS runtime.

The web docs at `https://openmagi.ai/docs` are expected to render these public
docs instead of carrying a separate product-docs copy.

## Quick Install

```bash
brew install openmagi/tap/magi-agent
magi-agent serve --port 8080
open http://localhost:8080/dashboard
```

The same package installs the terminal interface:

```bash
magi --help
magi-agent --help
```

## What Magi Agent Is

Magi Agent is a local work-agent runtime with a terminal CLI, HTTP API, and web
dashboard. It wraps model calls with runtime policy: bounded context, tool
permissions, receipts, evidence, repair behavior, and public-safe projection.

Use these docs to answer four questions:

- How do I install and run the agent locally?
- How do I configure models, credentials, tools, and workspace boundaries?
- How do runtime contracts, hooks, skills, memory, and automation make work
  checkable?
- How do I operate the local server safely in development or self-hosted
  environments?

## Start

- [Getting Started](getting-started.md)
- [Quickstart](quickstart.md)
- [CLI](cli.md)

## Configure

- [Configuration](configuration.md)
- [Customization](customization.md)
- [Runtime](runtime.md)
- [Tools](tools.md)
- [Contracts](contracts.md)
- [Hooks](hooks.md)

## Operate

- [Memory](memory.md)
- [Skills](skills.md)
- [Automation](automation.md)
- [Integrations](integrations.md)
- [API](api.md)
- [Deployment](deployment.md)

## Reference

- [Security](security.md)
- [Architecture](architecture.md)
- [Reference](reference.md)
- [Troubleshooting](troubleshooting.md)

## Machine-readable docs

- [llms.txt](llms.txt)
- [llms-full.txt](llms-full.txt)
