---
name: WeatherLookup
permission: net
description: "Look up current weather conditions for a city"
version: "0.1.0"
---

# WeatherLookup

Fetches current weather conditions for a given city using the wttr.in
free API. No API key required.

## Usage

```yaml
tools:
  overrides:
    WeatherLookup:
      timeoutMs: 15000
```
