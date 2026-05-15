---
name: web-search
description: Use when you need to search the web for information, news, facts, or current events. Provides web search via platform search proxy (Brave Search API) with browser fallback.
metadata:
  author: openmagi
  version: "2.0"
---

# Web Search

## Method 1: Native Platform Search (PRIMARY)

Use the native `WebSearch` / `web_search` tool when it is available. It calls the platform search proxy directly and is the **primary** web search method for current, online, recent, or source-sensitive facts.

## Method 1b: Platform Search Proxy via `web-search.sh`

When working from Bash or following shell-based skill instructions, use the same platform search proxy through the shell wrapper:

```bash
$BIN_DIR/web-search.sh "your search query"
```

This calls the platform search proxy (Brave Search API) with metered quota tracking. Returns JSON with web results. Auth is handled automatically by the platform — **never ask the user for API keys.**

Transport retry/backoff for transient failures is runtime-owned. A single timeout or 5xx is not a reason to give up immediately; let the shared transport layer retry first, then report the remaining failure honestly if it still does not recover.

## Method 2: Browser tool with Chromium (fallback)

If neither `web_search` nor `web-search.sh` works, use the `browser` tool:

**Quick search:**
1. Navigate to `https://www.google.com/search?q=YOUR+QUERY` (spaces as `+`)
2. Read search results from the page
3. Click relevant links for details

**URL patterns:**
- Web: `https://www.google.com/search?q=query+terms`
- News: `https://www.google.com/search?q=query&tbm=nws`
- Past day: append `&tbs=qdr:d`
- Past week: append `&tbs=qdr:w`

## Method 4: `web_fetch` (for known URLs)

Use `web_fetch` when you already know the URL.

## Best Practices

- Prefer native `WebSearch` / `web_search` first; use `web-search.sh` when working from Bash; then browser as fallback
- **Never tell the user their API key is missing** — platform handles auth automatically
- For transient transport errors, retry through the shared runtime path before escalating
- Use browser (Method 2) only as last resort — slower and rate-limited
- Don't search more than 3 times per minute via browser
- Cache results — don't re-search the same query
- For research, combine multiple searches with different keywords
