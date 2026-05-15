---
name: social-browser
description: Use when the user asks the bot to read visible Instagram or X/Twitter pages from a one-time chat or dashboard browser session without API/OAuth access.
metadata:
  author: openmagi
  version: "1.0"
---

# Social Browser

Read visible Instagram/X pages through the native `SocialBrowser` tool after the user opens a one-time session from chat or Dashboard > Integrations.

## Allowed

- Check whether a one-time social browser session is connected.
- Open provider-scoped Instagram/X URLs.
- Read the currently visible page with `snapshot` or `scrape_visible`.
- Save a screenshot under the workspace when the user asks.
- Close the one-time session when finished.

## Not Allowed

- Do not ask for, collect, store, replay, or infer social-network passwords.
- Do not bypass rate limits, CAPTCHA, login challenges, paywalls, or provider access controls.
- Do not do bulk crawling. Keep reads to visible-page, user-requested context.
- Do not post, like, follow, DM, delete, or otherwise mutate social accounts from this tool.

## Tool

Use native `SocialBrowser`:

```json
{"action":"status","provider":"x"}
{"action":"open","provider":"instagram","url":"https://www.instagram.com/"}
{"action":"scrape_visible","provider":"x","maxItems":20}
{"action":"snapshot","provider":"instagram"}
{"action":"screenshot","provider":"x","path":"screens/x-home.jpg"}
{"action":"close","provider":"x"}
```

If no session is connected, call `SocialBrowser` anyway. The runtime will ask the user to open Instagram or X in a one-time chat browser session. Do not request their password in chat.
