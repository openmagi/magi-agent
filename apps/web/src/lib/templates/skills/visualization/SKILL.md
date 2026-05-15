---
name: visualization
description: "Render charts, images, and interactive HTML artifacts in the chat UI. Use when a response benefits from visual output: 차트, 그래프, 시각화, 대시보드, 비교표, 분포, 추이, chart, graph, plot, visualize, dashboard, 인포그래픽. Three tracks — inline ECharts for standard charts (bar/line/pie/scatter/candlestick/heatmap), matplotlib PNG for statistical figures (seaborn, complex compositions), HTML artifact for interactive dashboards or multi-section reports."
user_invocable: false
metadata:
  author: openmagi
  version: "1.0"
---

# Visualization — Charts, Images, HTML Artifacts

Make your answers **visual** when the data is visual. The chat UI renders
three kinds of visual output natively. Pick the right track.

## Decision Tree (use in this order)

```
Is the output a single standard chart (bar / line / pie / scatter /
area / radar / candlestick / heatmap)?
  YES → Track A: inline ECharts fence        ← default, pick this first
  NO ↓

Is the output a complex statistical figure (seaborn regplot, pairplot,
correlation matrix with custom styling, scientific plot) or an image
composition / annotation?
  YES → Track B: matplotlib/PIL PNG + attachment
  NO ↓

Is the output an interactive dashboard, multi-section report, or tool
the user will want to scroll / click through separately from the chat?
  YES → Track C: HTML artifact file (.html attachment)
```

**Prefer A over B over C.** Token cost and render speed both favor
Track A. Only escalate when A genuinely cannot express what's needed.

---

## Track A — Inline ECharts Fence (default)

Emit a fenced code block with language tag `echarts` containing a valid
[ECharts option JSON](https://echarts.apache.org/en/option.html). The
web/mobile chat UI renders it inline automatically.

**Format** (literal — copy this structure, do not wrap in text):

````
```echarts
{
  "title": { "text": "Title here" },
  "xAxis": { "type": "category", "data": ["..."] },
  "yAxis": { "type": "value" },
  "series": [{ "type": "bar", "data": [] }]
}
```
````

### Copy-paste examples

**Bar chart — monthly revenue**

````
```echarts
{
  "title": { "text": "월별 매출 (만원)", "left": "center" },
  "tooltip": { "trigger": "axis" },
  "xAxis": { "type": "category", "data": ["1월","2월","3월","4월","5월","6월"] },
  "yAxis": { "type": "value" },
  "series": [{ "name": "매출", "type": "bar", "data": [820, 932, 901, 934, 1290, 1330], "itemStyle": { "color": "#7C3AED" } }]
}
```
````

**Line chart — time-series trend**

````
```echarts
{
  "title": { "text": "일간 활성 사용자" },
  "tooltip": { "trigger": "axis" },
  "xAxis": { "type": "category", "data": ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"] },
  "yAxis": { "type": "value" },
  "series": [{ "type": "line", "smooth": true, "data": [120, 132, 101, 134, 90, 230, 210], "areaStyle": {} }]
}
```
````

**Pie — share of X**

````
```echarts
{
  "title": { "text": "채널별 유입", "left": "center" },
  "tooltip": { "trigger": "item" },
  "series": [{
    "type": "pie", "radius": ["40%", "70%"],
    "data": [
      { "value": 1048, "name": "Google" },
      { "value": 735,  "name": "Direct" },
      { "value": 580,  "name": "Email" },
      { "value": 484,  "name": "Social" },
      { "value": 300,  "name": "Referral" }
    ]
  }]
}
```
````

**Candlestick — trading / price**

````
```echarts
{
  "xAxis": { "type": "category", "data": ["2026-04-15","2026-04-16","2026-04-17","2026-04-18","2026-04-19"] },
  "yAxis": { "type": "value", "scale": true },
  "series": [{ "type": "candlestick", "data": [[20,34,10,38],[40,35,30,50],[31,38,33,44],[38,15,5,42],[20,34,10,38]] }]
}
```
````

(Candlestick data order: `[open, close, low, high]`.)

**Heatmap — correlation / calendar**

````
```echarts
{
  "tooltip": { "position": "top" },
  "grid": { "height": "60%" },
  "xAxis": { "type": "category", "data": ["Mon","Tue","Wed","Thu","Fri"], "splitArea": { "show": true } },
  "yAxis": { "type": "category", "data": ["9am","12pm","3pm","6pm"], "splitArea": { "show": true } },
  "visualMap": { "min": 0, "max": 100, "calculable": true, "orient": "horizontal", "left": "center", "bottom": "5%" },
  "series": [{ "type": "heatmap", "data": [[0,0,10],[1,0,30],[2,0,50],[3,0,70],[4,0,90],[0,1,20],[1,1,40],[2,1,60],[3,1,80],[4,1,95]], "label": { "show": true } }]
}
```
````

**Multi-series comparison**

````
```echarts
{
  "title": { "text": "매출 vs 원가" },
  "tooltip": { "trigger": "axis" },
  "legend": { "data": ["매출", "원가"], "bottom": "0" },
  "xAxis": { "type": "category", "data": ["Q1","Q2","Q3","Q4"] },
  "yAxis": { "type": "value" },
  "series": [
    { "name": "매출", "type": "bar", "data": [1200, 1500, 1700, 2100] },
    { "name": "원가", "type": "bar", "data": [800, 950, 1100, 1300] }
  ]
}
```
````

### Rules for ECharts fences

- **Valid JSON only** — strings in double quotes, no trailing commas, no comments.
- **Do not wrap the JSON in any narration inside the fence.** The whole fence body is parsed as JSON.
- **Include a title** when the chart is the main answer. Omit only when context makes it obvious.
- **Include `tooltip`** for all but the simplest charts — it's cheap and greatly improves UX.
- **Prefer `series[i].type`** over global type so multi-series charts work.
- **Do not reference external images or fonts** — the chart must be self-contained JSON.
- **Do not use `formatter` as a function** — use string templates like `"{b}: {c}"` (JSON can't carry JS functions).

---

## Track B — matplotlib / PIL PNG

Use when the figure is too complex for ECharts option JSON: seaborn plots,
subplots with custom annotations, statistical plots, image compositions.

Write the PNG to workspace; the attachment pipeline delivers it inline.

```bash
python3 - <<'PY'
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import seaborn as sns
import numpy as np

# ... your figure ...
fig, ax = plt.subplots(figsize=(10, 6))
sns.heatmap(np.random.rand(10, 10), annot=True, ax=ax)
ax.set_title("Correlation matrix")

out = "/home/ocuser/.openclaw/workspace/corr-$(date +%s).png"
fig.savefig(out, dpi=150, bbox_inches="tight")
print(out)
PY
```

Then attach the output file via the standard file-send flow. The chat UI
renders it inline automatically.

---

## Track C — HTML Artifact (interactive / long-form)

Use for dashboards, multi-section reports, or interactive tools. The file
is rendered in a **sandboxed iframe side panel** (desktop) or fullscreen
modal (mobile) when the user clicks it.

### How to emit an HTML artifact

1. Write a **self-contained** `.html` file to workspace. You may include
   CDN `<script>` / `<link>` tags for libraries like Tailwind, Chart.js,
   ECharts (CDN), D3 — but no calls to your own backend.
2. Deliver the file via the normal file-send flow. The user sees an
   "Interactive HTML — click to open" card and the artifact opens in a
   sandboxed panel.

### HTML artifact constraints

- **Self-contained**: all JS and CSS inline or from public CDN.
  The iframe cannot call `openmagi.ai` APIs (null-origin sandbox).
- **No cookies / localStorage access to parent** — the sandbox explicitly
  blocks this. Do not rely on auth / user-session from within the HTML.
- **Scripts allowed**: `sandbox="allow-scripts"` is set. Forms, popups,
  top-navigation are blocked. Use client-side state only.
- **Mobile**: the artifact opens fullscreen on mobile — design for narrow
  viewports too.

### Minimal template

```html
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Q1 대시보드</title>
<script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-50 p-6">
  <h1 class="text-2xl font-bold mb-4">Q1 2026 Summary</h1>
  <div class="grid grid-cols-2 gap-4">
    <div class="bg-white rounded-xl p-4 shadow">
      <div class="text-sm text-gray-500">매출</div>
      <div class="text-3xl font-semibold">₩12.4M</div>
    </div>
    <div class="bg-white rounded-xl p-4 shadow">
      <div class="text-sm text-gray-500">MAU</div>
      <div class="text-3xl font-semibold">8,293</div>
    </div>
  </div>
</body>
</html>
```

Save with a descriptive filename like `q1-dashboard.html` — the filename
becomes the card title in the chat.

---

## When NOT to use this skill

- Plain text answers (no visual element needed) — just reply normally.
- Tables — markdown tables are fine for small data (<20 rows, ≤5 cols).
  Use an ECharts chart only if the relationship/trend is the point.
- A single number or metric — write it in text, don't spin up a chart.

## Guard against bad output

- **Never** emit a truncated/partial ECharts JSON — either finish the fence
  or don't emit one at all. A truncated fence renders as a parse error.
- **Never** reference colors / fonts that only exist on your local machine.
- **Never** put thinking/commentary inside the fenced block — only JSON.
