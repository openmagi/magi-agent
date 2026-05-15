# Stop Praying Your Agent Follows the Prompt. Enforce It.

## Every agent today runs on hope

Here is how every AI agent works in 2026: you write a system prompt. You tell the model what to do, what not to do, how to behave, what to check before answering. Then you deploy it and hope.

Hope it reads the instructions carefully. Hope it does not skip the verification step you described in paragraph four. Hope it does not hallucinate numbers when the database is right there. Hope it does not promise to "send results later" when no mechanism exists to do that. Hope it does not claim the tests passed without running them.

Prompts are suggestions. The model can ignore any of them, at any time, for any reason. It usually follows most of them. But "usually" and "most" are not engineering. They are prayer.

This is fine when a developer is watching. You see the bad output, you correct it, you move on. But the moment your agent runs autonomously -- serving customers at 3am, posting scheduled reports, processing documents in a pipeline -- prayer stops being a strategy. Nobody is watching. The prompt is all you have. And the prompt is not enough.

## What if your rules were not suggestions?

Magi starts from a different premise: the rules you write should be enforced by the runtime, not suggested to the model. The model cannot skip them. It cannot forget them. It cannot decide they do not apply to this particular request.

You define what "correct" means for your domain. The runtime makes sure every output meets that definition before it reaches the user.

This is not a better prompt. It is not a meta-prompt that watches the first prompt. It is code. Your rules become hooks that the runtime executes on every turn, every tool call, every response. The model's output is treated as a draft. The draft passes through your rules. If it fails, the model tries again with specific feedback about what went wrong. If it passes, it ships.

The model never sees the enforcement layer. It cannot argue with it, work around it, or creatively reinterpret it. Your rules run after the model finishes thinking, in deterministic code, before the output is committed.

## What "programmable" means in practice

Magi exposes the full agent lifecycle as hookpoints -- moments where your code runs and makes decisions. Before the model is called. After it responds. Before a tool is executed. Before the final output is committed. You write rules that attach to these moments.

Some examples of what rules look like, without getting into implementation details:

You can define that any response citing a file must have actually opened that file during the turn. If the model references "config.yaml" without reading it, the response is blocked and the model is told to go read it first.

You can define that scheduled reports must contain data from actual tool calls -- database queries, API responses, file reads -- and not from the model's training data. If the model generates plausible-looking numbers without querying the source, it gets caught.

You can define that the model cannot promise future delivery. If it says "I will send you the results when the analysis is complete" and then tries to end the turn, the rule blocks it and says: finish the work now.

You can disable any built-in rule that does not fit your use case. A casual chatbot does not need numeric verification. A financial analyst bot needs it on every turn. You decide.

The key property is that these are not guidelines the model interprets. They are gates the model passes through. The difference matters. A guideline says "please verify your claims." A gate checks whether claims are verified and rejects the ones that are not.

## Why this matters now

AI agents are crossing a threshold. They are moving from tools a human operates to services that operate alone. Scheduled tasks. Background pipelines. Customer-facing bots running around the clock. Multi-step workflows that take hours.

When a human is in the loop, prompts are fine. The human is the verification layer. But when agents work alone, you need a different kind of guarantee. You need your rules to be part of the infrastructure, not part of the conversation.

The analogy is databases. Nobody writes "please make sure this transaction is atomic" in a comment and hopes the database respects it. You use transactions. The database enforces atomicity whether or not your application code remembers to ask nicely. Agent runtimes should work the same way: your correctness requirements should be structural, not aspirational.

We run thirty-plus bots in production on Kubernetes, serving real users on scheduled tasks with no human in the loop. The enforcement layer does not catch everything -- no system does. But the gap between "hope the prompt works" and "enforce the rules in code" is the gap between a prototype and a production system.

## The direction

Magi is open source under Apache 2.0. Provider-neutral -- it runs on Claude, GPT, Gemini, or any model that supports tool use. Your domain, your rules, your definition of correct.

The runtime, the full hook system, and the configuration format are available at [github.com/openmagi/magi-agent](https://github.com/openmagi/magi-agent).
