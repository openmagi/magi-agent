---
name: telegram-file-output
description: Use ONLY when delivering files to a Telegram chat. If the system context shows `[Channel: <name>]` (web/app channel), STOP — use `file-send.sh` with that channel name instead. This skill is for Telegram-only delivery and hard-codes the Telegram Bot API.
metadata:
  author: openmagi
  version: "3.0"
---

# File Output — Channel-Aware

You can deliver files to the user as attachments. **The delivery mechanism depends on the channel.** Check the system context first.

## STEP 1 — Detect the channel (MANDATORY)

Look at the system messages at the top of the conversation:

| What you see | Channel type | Delivery method |
|--------------|--------------|-----------------|
| `[Channel: <name>]` present | Web / mobile app channel | **Use `file-send.sh`** (see Section A) |
| No `[Channel: ...]` hint | Telegram (legacy single-channel bot) | Use Telegram Bot API (see Section B) |

**If you see `[Channel: ...]` and still send to Telegram, the file goes to the wrong place and the user does not receive it.** Always check first.

---

## Section A — Web / App Channels (use this when `[Channel: ...]` is present)

Use the `file-send.sh` wrapper (always in PATH). Pass the channel name from the `[Channel: <name>]` hint as the second argument.

### Quick recipe

```bash
# 1. Write the file
cat > /tmp/output.md << 'FILEEOF'
Your content here
FILEEOF

# 2. Send it to the current channel (replace "general" with the name from [Channel: ...])
file-send.sh /tmp/output.md general

# 3. The script prints an attachment marker like [attachment:abc123:output.md].
#    Include that marker verbatim in your chat reply:
#    "여기 요청하신 파일입니다: [attachment:abc123:output.md]"

# 4. Clean up
rm /tmp/output.md
```

### Example A1 — Markdown report in the `news` channel

```bash
cat > /tmp/report.md << 'FILEEOF'
# Market Research Report
## Key Findings
...
FILEEOF

MARKER=$(file-send.sh /tmp/report.md news)
echo "$MARKER"   # e.g. [attachment:7f3c...:report.md]
rm /tmp/report.md
```

Then in your chat reply:
```
리포트 준비 완료했습니다.
[attachment:7f3c...:report.md]
```

### Example A2 — CSV export in the channel from the hint

```bash
# [Channel: finance] was shown in system context
cat > /tmp/data.csv << 'FILEEOF'
name,value,date
Item A,100,2026-04-18
FILEEOF

MARKER=$(file-send.sh /tmp/data.csv finance)
echo "$MARKER"
rm /tmp/data.csv
```

### Example A3 — Image (PNG)

```bash
# [Channel: general]
MARKER=$(file-send.sh /tmp/chart.png general)
echo "$MARKER"
# Client renders PNG inline
```

### Rules for Section A
- **Never** call `api.telegram.org` when `[Channel: ...]` is present — it silently delivers to Telegram instead of the web/app chat the user is reading.
- Never use the `message` tool's `send` action for files — it always fails.
- Always include the attachment marker in your reply text so the client renders it.
- Max file size: 50MB.
- Supported: jpg/png/gif/webp (inline), pdf/txt/csv/md/html/json/docx/xlsx/zip (file card).

---

## Section B — Telegram-only bots (no `[Channel: ...]` hint)

Only use this path when there is **no channel hint** in system context (legacy Telegram-only bots).

### Telegram recipe

```bash
cat > /tmp/output.md << 'FILEEOF'
Your content here
FILEEOF

BOT_TOKEN=$(node -e "const c=JSON.parse(require('fs').readFileSync('/home/ocuser/.openclaw/openclaw.json','utf8')); console.log(c.channels.telegram.botToken)")
CHAT_ID=$(node -e "const c=JSON.parse(require('fs').readFileSync('/home/ocuser/.openclaw/credentials/telegram-default-allowFrom.json','utf8')); console.log(c.allowFrom[0])")

curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendDocument" \
  -F "chat_id=${CHAT_ID}" \
  -F "document=@/tmp/output.md" \
  -F "caption=Here is the file you requested."

rm /tmp/output.md
```

### Key paths (Telegram only)

| What | Path |
|------|------|
| Bot token | `/home/ocuser/.openclaw/openclaw.json` → `channels.telegram.botToken` |
| Owner chat_id | `/home/ocuser/.openclaw/credentials/telegram-default-allowFrom.json` → `allowFrom[0]` |

### Photos (Telegram only)

```bash
curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendPhoto" \
  -F "chat_id=${CHAT_ID}" \
  -F "photo=@/tmp/chart.png" \
  -F "caption=Chart"
```

### Rules for Section B
- Only reachable when there is **no** `[Channel: ...]` system message.
- Max file size: 50MB via Bot API.
- Always clean up temp files.
- Never expose the bot token in chat output — only use it inside `system.run`.

---

## When to send as file (applies to both sections)

| Situation | Action |
|-----------|--------|
| User asks for a raw file (.md, .csv, .json, .txt, etc.) | **Always send as file** |
| Output exceeds ~3000 characters | **Send as file** |
| Research reports, analysis documents | **Send as file** |
| Code files, scripts, configs | **Send as file** |
| Structured data (tables, lists, datasets) | **Send as file** |
| User says "send file", "attach", "download", "파일로 줘" | **Send as file** |
| Short conversational answer | Send as text (normal) |

## Fallback
If the chosen delivery method fails (e.g. `file-send.sh` not in PATH, or Telegram config unreadable), fall back to inline text output and tell the user you couldn't attach the file.
