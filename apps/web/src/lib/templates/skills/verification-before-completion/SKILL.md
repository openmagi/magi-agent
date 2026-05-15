---
name: verification-before-completion
description: Use when about to claim work is complete, fixed, or passing, before committing or creating PRs - requires running verification commands and confirming output before making any success claims; evidence before assertions always
---

# Verification Before Completion

## Overview

Claiming work is complete without verification is dishonesty, not efficiency.

**Core principle:** Evidence before claims, always.

**Violating the letter of this rule is violating the spirit of this rule.**

## The Iron Law

```
NO COMPLETION CLAIMS WITHOUT FRESH VERIFICATION EVIDENCE
```

If you haven't run the verification command in this step, you cannot claim it passes.

## The Gate Function

```
BEFORE claiming any status or expressing satisfaction:

1. IDENTIFY: What command proves this claim?
2. RUN: Execute the FULL command (fresh, complete)
3. READ: Full output, check exit code, count failures
4. VERIFY: Does output confirm the claim?
   - If NO: State actual status with evidence
   - If YES: State claim WITH evidence
5. ONLY THEN: Make the claim

Skip any step = lying, not verifying
```

## Common Failures

| Claim | Requires | Not Sufficient |
|-------|----------|----------------|
| Tests pass | Test command output: 0 failures | Previous run, "should pass" |
| Build succeeds | Build command: exit 0 | Linter passing, logs look good |
| Bug fixed | Test original symptom: passes | Code changed, assumed fixed |
| Config works | Health check + test message | Config file looks correct |
| Deploy done | End-to-end test via Telegram | Services restarted |

## Red Flags -- STOP

- Using "should", "probably", "seems to"
- Expressing satisfaction before verification
- About to commit without verification
- Relying on partial verification
- Thinking "just this once"
- ANY wording implying success without having run verification

## Rationalization Prevention

| Excuse | Reality |
|--------|---------|
| "Should work now" | RUN the verification |
| "I'm confident" | Confidence != evidence |
| "Just this once" | No exceptions |
| "Partial check is enough" | Partial proves nothing |

## Verification by Task Type

**Config changes:**
```
system.run ["systemctl", "--user", "status", "openclaw-gateway-soulclaw"]
system.run ["curl", "-s", "http://127.0.0.1:18791/health"]
# Test Telegram message
```

**Script changes:**
```
system.run ["bash", "-n", "script.sh"]  # syntax check
# Dry-run or actual execution with expected output check
```

**Python changes:**
```
system.run ["pytest"]  # full test suite
```

**Deploy:**
```
# Health checks + end-to-end Telegram test
```

## The Bottom Line

Run the command. Read the output. THEN claim the result.

This is non-negotiable.
