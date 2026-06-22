# AGENTS.md

<!-- README for AI coding agents. Keep this file concise — every line is loaded into
     context on every session. Aim for <150 lines. Update it in the same PR/commit
     that introduces or changes a convention. -->

## Project Overview

This is a script built with python. It searchs all European budget flights according to your departure point and time range.

## Tech Stack

| Layer       | Technology        | Version |
|-------------|-------------------|---------|
| Language    | Python            |         |
| Framework   |                   |         |
| Database    |                   |         |
| Test runner |                   |         |
| Linter      |                   |         |
| Formatter   |                   |         |

## Setup & Commands

```bash
# Install dependencies
<command>

# Run development server / start the app
<command>

# Run the full test suite
<command>

# Run a single test / file
<command>

# Lint
<command>

# Type-check
<command>

# Build for production
<command>
```

## Project Layout

```
/
├── src/          # Application source
│   ├── ...
├── tests/        # Tests — mirrors src/ structure
├── docs/         # Human-facing documentation
└── scripts/      # One-off tooling
```

<!-- Generated / compiled output: -->
Do not edit files under `dist/`, `build/`, or `generated/` — they are auto-generated.

## Security

- Never read, log, or commit secrets, API keys, or credentials.
<!-- - Do not modify files in `<sensitive-path>` without an explicit instruction to do so. -->
<!-- - Any other security constraint specific to this repo -->

## Agent Behaviour

- Confirm scope before writing code when the task is ambiguous.
- Make the smallest change that satisfies acceptance criteria.
- Do not introduce new dependencies without asking first.
- Summarise what changed and why at the end of each session.
