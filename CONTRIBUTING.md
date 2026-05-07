# Contributing to Magi

Thank you for your interest in contributing!

## Development Setup

```bash
git clone https://github.com/openmagi/magi-agent.git
cd magi-agent
npm install
npm run dev
```

## Running Tests

```bash
npm test            # Run all tests
npm run test:watch  # Watch mode
npm run lint        # Type check
```

## Code Style

- TypeScript strict mode
- No `any` type
- Explicit return types for exported functions
- `const` over `let`
- Files: kebab-case
- Components/classes: PascalCase
- Constants: UPPER_SNAKE_CASE

## Pull Requests

1. Fork the repo and create a feature branch
2. Write tests for new functionality
3. Ensure `npm test` and `npm run lint` pass
4. Submit a PR with a clear description

## Reporting Issues

Use [GitHub Issues](https://github.com/openmagi/magi-agent/issues). Include:
- Steps to reproduce
- Expected vs actual behavior
- Node.js version and OS
