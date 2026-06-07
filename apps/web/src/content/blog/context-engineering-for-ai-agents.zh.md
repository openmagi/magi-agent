---
title: "AI Agent 的 Context Engineering：为什么你的 Agent 越用越笨（以及如何解决）"
description: "AI Agent 不只是会遗忘，它们会被自己的 context 淹没。从压缩陷阱到 RAG 的局限性，我们深入分析了为什么 context engineering 是 agent 基础设施中最难解决的问题，并介绍 Hipocampus：我们开源的多层记忆系统。"
date: "2026-03-17"
tags: ["Context Engineering", "AI Agent", "LLM", "Memory", "Hipocampus", "Open Source"]
locale: "zh"
author: "openmagi.ai"
---

在 AI Agent 领域，有一个没人愿意公开谈论的秘密。

你的 Agent 不只是会遗忘。使用时间越长，它的表现反而越差。不是因为模型退化了，而是因为它的 context 在退化。

如果你运行过一个长期使用的 AI Agent，发现它变得越来越慢、越来越贵、越来越不准确，那你已经亲身体验过这个问题了。原因不在模型本身，而在模型**周围的一切**。

这就是 **context engineering** 的问题。可以说，它是构建生产级 AI Agent 中最重要的未解难题。

---

## Context 到底是什么？

当你向 AI Agent 发送一条消息时，你的消息并不是唯一的输入。实际的输入大概是这样的：

```
[System Prompt]
You are an AI marketing assistant...

[User Profile]
This user runs a small e-commerce business...

[Active Task State]
Currently working on Q1 ad campaign analysis...

[Conversation History]
User: Can you pull the ROAS data for January?
Agent: Here's what I found...
User: Good. Now compare it with December.

[Tool Call Results]
Google Ads API response: { "roas": 3.2, "spend": 12400, ... }
Analytics data: { "sessions": 45200, "conversion_rate": 0.032, ... }

[Current Message]
User: What should we change for February?
```

所有这些内容在每次 API 调用时都会被打包成一个输入。LLM 从头到尾读一遍，然后生成回复。它不会"记住"之前的调用，它只是在引用当前 context window 中的内容。

两个关键的推论：

1. **Context 中的一切都要消耗 token。** 系统提示、对话历史、工具返回结果，每一次 API 调用都要计费。
2. **Context 中的一切都在争夺注意力。** LLM 通过 Attention 机制同时计算所有 token 之间的关系。无关信息越多，注意力就越分散。重要的信号被噪音淹没。

Context 同时决定了你的 Agent 的**成本**和**质量**。每一个放进去的 token，不是在帮忙，就是在帮倒忙。

---

## Context 累积问题

接下来的情况开始变得棘手了。

大多数人以为 context 累积就是"对话变长了"。这只是问题的一小部分。

来看一个真实场景：你让 Agent 调研竞品定价。

为了回答这一个问题，Agent 可能需要：
1. 搜索 5 个竞品的网站
2. 抓取定价页面（完整 HTML 转换为 markdown）
3. 读取你自己的定价历史文档
4. 从电子表格中提取数据
5. 分析结果并撰写总结

等它交付答案的时候，context 中已经包含了：
- 5 个竞品网站的数据
- 你的内部定价文档
- 电子表格数据
- Agent 的分析和推理过程
- 所有中间步骤的工具调用结果

这可能有 **50,000+ token** 的调研数据留在 session context 中。

然后你说："很好，谢谢。能不能帮我写一封关于明天站会的邮件？"

一个完全无关的任务。但那 50,000 个 token 的竞品定价数据**仍然在 context 中**。它们仍在被计费，仍在争夺模型的注意力。

Agent 现在一边写站会邮件，一边还在"思考"竞品定价数据。邮件质量下降了，成本翻倍了。你和 Agent 都没意识到原因。

**这就是根本问题：context 默认是只追加不删除的。** 每一次工具调用、每一个搜索结果、每一个中间步骤都会留下来。任务之间互相干扰。成本不断叠加。质量持续下降。

而且，情况只会变得更糟。

---

## 尝试一：压缩（Compaction）

最显而易见的解决办法是压缩。当 context 太长时，让 LLM 把它总结一下。

大多数 Agent 框架都支持这个功能。当对话达到某个阈值（比如 context window 的 80%），整个历史记录被压缩成一个摘要。重新开始，context 变小了。

听起来很优雅。但在实践中，它有两个致命缺陷。

### Context 漂移

摘要的摘要的摘要，信息会指数级丢失：

- **第一轮：** "用户是一名 React 开发者，正在用 TypeScript 做一个 Next.js 项目，专注于 Server Components。"
- **第二轮：** "用户从事 Web 开发。"
- **第三轮：** "用户在科技行业工作。"

仅仅 2-3 轮压缩，关键细节就蒸发了。

### 无法区分重要性

压缩对所有信息一视同仁。但信息的重要性并不相同：

- "用户有严重的花生过敏" ——关乎生命安全，几个月后可能还需要用到
- "用户今天问了天气" ——明天就没用了

压缩无法区分这两者。它对所有内容应用相同的压缩比。关键信息和闲聊一起被丢弃了。

**压缩本质上是没有优先级机制的有损压缩。** 它能争取一些时间，但解决不了根本问题。

---

## 尝试二：结构化 Context 文件

一个更好的方案：与其把所有内容留在对话历史中，不如把重要信息写入结构化文件。

这就是大多数成熟 Agent 架构中使用的 `.md` 文件模式：

- **`MEMORY.md`** ——关于用户和项目的长期信息（约 50 行）
- **`SCRATCHPAD.md`** ——当前工作状态和活跃任务（约 100 行）
- **`AGENTS.md`** ——行为规则和指令（约 500 行）

Agent 在每次 session 开始时读取这些文件。核心信息保存在持久化文件中，可以跨 session 存活，而不是依赖那些会被压缩和降级的对话历史。

这是一个巨大的改进。但它带来了新的问题：

**体积压力。** 这些文件在每次 API 调用时都会被加载。500 行的 AGENTS.md 意味着每一条消息都要为这 500 行 token 付费。如果把 MEMORY.md 扩充到 200 行的详细笔记，那就是每次调用都要为 200 行额外内容付费，即使用户只是说了一声"你好"。

**维护负担。** 必须有人（Agent 或用户）来决定哪些内容该写入这些文件。放太多，成本爆炸，注意力被稀释。放太少，关键信息被遗漏。

**扁平结构。** 一个 MEMORY.md 文件没有层级关系。这些信息是昨天的？上个月的？还有用吗？不读完所有内容根本无从判断。

结构化文件是必要的，但不够充分。它解决了"重要信息存在哪里"的问题，但没有解决"如何在正确的时间找到正确的信息"的问题。

---

## 尝试三：引入 RAG

RAG（Retrieval-Augmented Generation，检索增强生成）解决了搜索问题。你不需要把所有内容加载到 context 中，而是把知识存储在可搜索的索引中，只检索相关的部分。

把 Agent 积累的知识存入文件，用搜索引擎（BM25 关键词搜索、向量嵌入，或两者结合）建立索引。当 Agent 需要信息时，它搜索索引，只拉取相关的片段。

这很强大。一个拥有 10,000 份文档知识的 Agent，每次查询只需要加载最相关的 3-5 份。成本保持稳定，注意力保持集中。

但 RAG 也有自身的局限性：

**你需要知道搜什么。** RAG 在有明确查询时效果很好。但对于环境 context 呢？比如 Agent 应该"自然而然就知道"的那些东西：用户的时区、沟通偏好、正在进行的项目状态。你无法主动搜索这些，因为在你意识到需要它们之前，已经太晚了。

**索引延迟。** 当前 session 中写入的信息不会立刻进入搜索索引。Agent 下午 2 点学到了某个重要信息，但索引要到 session 结束后才更新。到那时，Agent 可能已经错过了需要这个信息的时机。

**没有时间感知。** RAG 返回语义最相关的结果，但它对时效性和衰减没有概念。三个月前的决策和今天上午的决策会被赋予同等权重。但在实际场景中，近期的 context 几乎总是更重要。

**冷启动问题。** 一个知识库为空的新 Agent 搜不到任何东西。RAG 只有在积累了足够的知识之后才能发挥作用。而积累知识恰恰需要它本应该提供的 context 管理能力。

---

## 真正的问题：没有人解决了完整的技术栈

每种方案各自解决了一个环节：

| 方案 | 解决了 | 遗漏了 |
|------|--------|--------|
| 压缩 | Context 溢出 | 信息丢失、无优先级机制 |
| 结构化文件 | 持久化记忆 | 扩展性、维护负担、扁平结构 |
| RAG | 基于搜索的检索 | 环境 context、时间感知、冷启动 |

但生产级 Agent 需要所有这些协同工作，并且还需要更多。它们需要一个系统能够：

1. 永久保留原始信息（不做有损压缩）
2. 在多个时间尺度上创建可搜索的索引
3. 在正确的时间加载正确的 context
4. 从第一天就能使用（无冷启动问题）
5. 无需人工维护，自动运行

这就是我们构建的东西。

---

## 压缩树（Compaction Tree）

核心洞察：**永远不要删除原始数据。在上层构建搜索索引。**

可以把它想象成图书馆。传统的压缩方式就像烧掉你的书，只留下目录。而压缩树保留书架上的每一本书，并在上面加一套检索目录系统。

```
memory/
├── ROOT.md                 ← 始终加载（约 100 行）
│                              主题索引："我是否知道关于 X 的事？"
├── monthly/
│   └── 2026-03.md          ← 月度关键词索引
│                              "三月份涉及的主题包括：..."
├── weekly/
│   └── 2026-W11.md         ← 周摘要
│                              关键决策、已完成任务
├── daily/
│   └── 2026-03-15.md       ← 日压缩节点
│                              主题、决策、结果
└── 2026-03-15.md            ← 原始日志（永久保留，不删除）
                               当天发生的所有事情的完整记录
```

**遍历模式：**

需要查找什么？从顶部开始：

1. **ROOT.md** ——查看主题索引。我是否知道"竞品定价"的信息？是的，三月份有记录。
2. **Monthly** ——三月索引显示竞品分析发生在第 11 周。
3. **Weekly** ——第 11 周摘要显示定价调研在 3 月 12 日。
4. **Daily** ——3 月 12 日节点记录了关键决策和发现。
5. **Raw** ——3 月 12 日原始日志有完整的、未压缩的原始记录。

这是对时间记忆的 **O(log n) 搜索**。你永远不需要读取超出必要的内容，但完整的细节随时可以通过层层深入来获取。

### 固定节点与临时节点

压缩节点有自己的生命周期：

- **临时（Tentative）** ——时间段仍在进行中。当新数据到来时，节点会被重新生成。今天的日节点是临时的。本周的周节点也是临时的。
- **固定（Fixed）** ——时间段已结束。节点被冻结，不再更新。上周的周节点就是固定的。

这意味着压缩树**从第一天就可以使用**。你不需要等一周过完才有周摘要。它会立即以临时状态创建，并随着新数据的到来而更新。

### 智能阈值

并非所有内容都需要 LLM 来总结。如果一份日志只有 50 行，直接原文复制到日节点不会增加任何成本，也不会丢失任何信息。只有当内容超过阈值时，才会启用 LLM 总结：

| 层级 | 阈值 | 低于阈值 | 高于阈值 |
|------|------|----------|----------|
| 原始 → 日 | 约 200 行 | 原文复制 | LLM 关键词密集摘要 |
| 日 → 周 | 约 300 行 | 拼接日节点 | LLM 摘要 |
| 周 → 月 | 约 500 行 | 拼接周节点 | LLM 摘要 |

低于阈值：零信息丢失。高于阈值：面向搜索召回优化的关键词密集压缩，而非叙事性可读性。

---

## Hipocampus：完整的系统

压缩树是数据结构。[**Hipocampus**](https://github.com/kevin-hs-sohn/hipocampus) 是围绕它构建的完整系统。这是一个三层 Agent 记忆协议，由我们开发，在生产环境中经过实战检验，并已开源。

### 三个层级

```
Layer 1 ——系统提示（始终加载，每次 API 调用）
  ├── ROOT.md          约 100 行   来自压缩树的主题索引
  ├── SCRATCHPAD.md    约 150 行   当前工作状态
  ├── WORKING.md       约 100 行   当前任务
  └── TASK-QUEUE.md    约 50 行    待办事项

Layer 2 ——按需加载（Agent 决定需要时才读取）
  ├── memory/YYYY-MM-DD.md    原始日志（永久保留）
  ├── knowledge/*.md           详细知识文件
  └── plans/*.md               任务计划

Layer 3 ——搜索（通过压缩树 + 关键词/向量搜索）
  ├── memory/daily/            日压缩节点
  ├── memory/weekly/           周压缩节点
  └── memory/monthly/          月压缩节点
```

**Layer 1** 回答"我现在在做什么？"。始终存在于 context 中，始终消耗 token，因此要严格控制体积。

**Layer 2** 回答"我了解哪些详细信息？"。不访问就不消耗 token，当 Agent 意识到需要更多上下文时按需加载。

**Layer 3** 回答"我以前见过这个吗？"。ROOT.md 的主题索引让 Agent 能一眼判断记忆中是否存在某个信息，而无需加载任何内容。如果存在，通过树遍历或关键词搜索来检索。

### Session 协议

Hipocampus 定义了两个必须执行的流程：

**Session 启动：** 在回复任何内容之前，Agent 加载 Layer 1 文件并运行压缩链（日 → 周 → 月 → Root）。这确保树是最新的，ROOT.md 反映了最新状态。

**任务完成 Checkpoint：** 完成任何任务后，Agent 向原始日志文件写入一条结构化记录：

```markdown
## Competitor Pricing Analysis
- request: Compare our pricing with top 5 competitors
- analysis: Scraped pricing pages, pulled internal data
- decisions: Recommended 15% reduction on starter tier
- outcome: Report delivered, shared with team
- references: knowledge/pricing-strategy.md
```

这是唯一的信息源头。其他一切内容，包括压缩节点、ROOT.md、主题索引，都是通过压缩链从这些原始日志派生出来的。

### ROOT.md 的优势

最强大的功能是 ROOT.md 的主题索引。它解决了"搜什么"的问题：

```markdown
## Topics Index
- pricing: competitor-analysis, Q1-review, starter-tier-reduction
- infrastructure: k8s-migration, redis-upgrade, node-scaling
- marketing: ad-campaign-Q1, landing-page-redesign, SEO-audit
```

当用户问到定价相关的问题时，Agent 不需要盲目搜索。它查看主题索引，发现定价信息确实存在，并准确知道该深入哪个时间段。如果某个主题不在索引中，Agent 就知道应该去外部搜索，而不是浪费时间在空记忆中翻找。

**这消除了"为了决定是否加载而先加载"的问题。** 这是基于 RAG 的记忆系统中最大的效率瓶颈。

### 主动转储

Hipocampus 不会等到任务完成才持久化 context。协议鼓励主动转储。当对话超过 20 条消息、做出重要决策、或 Agent 感觉 context 变大时，都应该主动写入。

这可以防止一种隐蔽但破坏性极强的故障模式：**平台级的 context 压缩。** 当托管平台压缩对话历史时（大多数平台在长 session 中都会这么做），任何没有转储的细节都会永久丢失。尽早写入，频繁写入。原始日志是只追加的，一个 session 中多次转储完全无害。

---

## 为什么这对 Agent 平台至关重要

大多数 Agent 平台把重点放在部署上。点个按钮，你的 bot 就上线了。

但部署大概只占问题的 5%。剩下 95% 是**运维**。在数周甚至数月的持续使用中，让 Agent 保持有用、准确、成本可控。

没有合理的 context engineering：
- Agent 的成本随使用量线性增长
- 随着无关信息在 context 中累积，质量不断下降
- 关键知识在压缩循环中丢失
- Agent 无法区分昨天的信息和三个月前的信息

在 [Open Magi](https://openmagi.ai)，我们构建 [Hipocampus](https://github.com/kevin-hs-sohn/hipocampus) 是因为我们自己需要它。我们在生产环境中运行着数百个 Agent，亲眼看着它们全都撞上了同一堵墙：头几天表现很好，然后逐渐变得昂贵、缓慢、健忘。

Hipocampus 现在是我们平台上每一个 Agent 的默认记忆系统。当你在 Open Magi 上部署一个 Agent 时，你得到的不只是一个带 API key 的聊天机器人。你得到的是完整的 context engineering 技术栈：层级化压缩、多层记忆、RAG 搜索，以及让 Agent 在数月连续运行中保持敏锐的 session 协议。

因为部署一个 Agent 很容易。*让它持续有用*才是真正困难的部分。

---

*Hipocampus 已开源。访问 [GitHub 仓库](https://github.com/kevin-hs-sohn/hipocampus)，在你自己的 Agent 中使用它。*

*这是关于生产级 AI Agent 基础设施系列文章的第一篇。下一篇我们将探讨：AI Agent OS 到底是什么样的，以及为什么 Agent 像应用程序一样需要操作系统。*
