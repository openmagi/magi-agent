---
name: model-gateway
description: Call different AI models (Gemini, GPT, Kimi, Claude variants) through the platform when the user explicitly requests a specific model. Uses platform credits — no API keys needed.
metadata:
  author: openmagi
  version: "1.0"
---

# Model Gateway — Cross-Model Calls

You can call **any AI model** available on the platform, not just your primary model. Use this **only when the user explicitly asks** for a specific model.

## When to Use

**YES — user explicitly requests:**
- "Gemini로 분석해봐", "GPT로 다시 해봐", "Claude Opus로 풀어봐"
- "다른 모델한테 물어봐", "Gemini 의견도 들어보자"
- "이거 GPT한테도 시켜봐"

**NO — never use on your own initiative:**
- You think a different model might be better — this is NOT your decision
- Routine tasks — always use your primary model for normal work
- "더 잘할 수 있을 것 같아서" — no autonomous model switching

## Available Models

| Model | Provider | Best For | Cost |
|-------|----------|----------|------|
| `claude-opus-4-6` | Anthropic | Deep reasoning, complex code, expert analysis | $$$ |
| `claude-sonnet-4-6` | Anthropic | Fast and capable, balanced | $$ |
| `claude-haiku-4-5` | Anthropic | Simple queries, fastest Anthropic | $ |
| `gpt-5.5-pro` | OpenAI | Highest-accuracy hard questions | $$$$ |
| `gpt-5.5` | OpenAI | Code execution, agentic tasks, tool use | $$$ |
| `gpt-5.4-mini` | OpenAI | General purpose, fast | $$ |
| `gpt-5.4-nano` | OpenAI | Cheapest, trivial tasks | ¢ |
| `gemini-3.1-pro-preview` | Google | Reasoning, multimodal, huge context | $$$ |
| `gemini-3.1-flash-lite-preview` | Google | Very fast, cost-effective | $ |
| `kimi-k2p6` | Fireworks/Moonshot | Reasoning, analysis | $$ |
| `local/gemma-fast` | Gemma 4 Fast (beta) | Fast local general-purpose calls | local |
| `local/gemma-max` | Gemma 4 Max (beta) | Local reasoning, coding, long-context tasks | local |
| `local/qwen-uncensored` | Qwen 3.5 Uncensored (beta) | Legitimate requests blocked by censored models | local |

## How to Call

```bash
# Call a specific model
integration.sh "llm/chat" '{"model":"gemini-3.1-pro-preview","messages":[{"role":"user","content":"사용자의 질문"}]}'

# Call a local beta model
integration.sh "llm/chat" '{"model":"local/gemma-fast","messages":[{"role":"user","content":"빠르게 요약해줘"}],"max_tokens":700,"reasoning_effort":"low"}'

# List available models
integration.sh "llm/models"
```

## Message Format

Messages use OpenAI Chat Completions format:

```json
{
  "model": "gemini-3.1-pro-preview",
  "messages": [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "이 코드를 분석해줘: ..."}
  ],
  "max_tokens": 4096,
  "temperature": 0.7
}
```

## Response Format

```json
{
  "model": "gemini-3.1-pro-preview",
  "content": "모델의 응답 내용...",
  "usage": {
    "input_tokens": 150,
    "output_tokens": 420
  }
}
```

## Multi-Model Comparison

When the user asks for opinions from multiple models:

```bash
GEMINI=$(integration.sh "llm/chat" '{"model":"gemini-3.1-pro-preview","messages":[{"role":"user","content":"질문"}]}')
GPT=$(integration.sh "llm/chat" '{"model":"gpt-5.5","messages":[{"role":"user","content":"질문"}]}')
```

Then parse each response's `content` field and present the comparison.

## Billing

- Calls use the bot owner's **platform credits** access path (same as normal usage)
- Cost varies by model — flagship hosted models cost more per token
- Local beta models are Max/Flex-only, zero-rated by the platform, and do not require a separate API key
- No separate API keys or wallet auth needed
- Hosted models use the same per-token rates as if the model were your primary model

## Rules

1. **User-explicit only** — Never call another model on your own initiative
2. **Transparent** — Tell the user which model you're calling and why
3. **Return the result** — Present the other model's response clearly, noting which model generated it
4. **No opinion on model quality** — Don't editorialize about which model is "better"
