---
name: consultation-transcript
description: Process client consultation audio recordings into transcript, memo, follow-up tasks, and Knowledge Base artifacts. Use for lawyer/accountant/customer consultation recordings, meeting recordings, STT, transcription, or 녹취/상담 녹음 requests.
user_invocable: true
metadata:
  author: openmagi
  version: "1.0"
  category: knowledge
---

# Consultation Transcript

Use this skill when the user attaches or references a consultation audio recording and wants it turned into searchable work product.

## How It Works

Audio files attached in chat are queued automatically by the platform. The worker creates:

- `transcript.md` — speaker/timestamp transcript
- `consultation-memo.md` — professional review memo
- `tasks.json` — structured follow-up tasks

Transcript and memo artifacts are saved to the Knowledge Base collection `Consultations` so they can be searched later with `knowledge-search`.

## Supported Input

Supported audio types: `mp3`, `m4a`, `wav`, `ogg`, `webm`.

If the user has not attached the audio yet, ask them to attach the recording file in chat. Do not ask for API keys or external ASR credentials; platform services handle processing.

## Professional Domains

Infer the memo emphasis from the user request:

- Legal: facts, timeline, issues, evidence, deadlines, follow-up questions, caveats
- Accounting/tax: reporting period, tax type, missing materials, deadlines, filings, risk items
- General: summary, client requests, next actions, open questions

Always treat generated notes as draft work product. Tell the user to review the transcript and memo before relying on them for client advice or filings.

## After Processing

When processing completes, use `knowledge-search` to retrieve the resulting documents if the user asks follow-up questions such as:

- "그 상담에서 필요한 자료가 뭐였지?"
- "지난번 녹취 요약 찾아줘"
- "client-call.m4a에서 다음 액션만 뽑아줘"

Search in the `Consultations` collection first.
