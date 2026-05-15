---
name: general-legal-research
description: Use for cross-jurisdiction legal research, comparative law analysis, or when research spans multiple legal systems. Provides source reliability scoring, comparative framework, counter-analysis, and quality gates. Routes to jurisdiction-specific skills (korean-law, us-legal, eu-regulatory, patent-ip).
user_invocable: true
---

# General Legal Research

Cross-jurisdiction legal research methodology. Use this skill when a question spans multiple legal systems, requires comparative analysis, or does not fit a single jurisdiction-specific skill.

For single-jurisdiction queries, route directly to the appropriate skill (see Cross-Reference below).

## Workflow

1. **Query interpretation** -- identify jurisdictions, legal domains, time frame, and the user's actual question.
2. **Jurisdiction mapping** -- determine which skills and APIs to invoke.
3. **Source collection** -- call jurisdiction-specific skills and external APIs.
4. **Source grading** -- apply the A-D reliability scoring to every source.
5. **Analysis and structuring** -- build an issue tree, cross-reference provisions, apply comparative framework if multi-jurisdiction.
6. **Quality gate** -- run the checklist before producing output.
7. **Output** -- with inline pinpoint citations and `[Unverified]` tags where needed.

## Source Reliability Scoring

Grade every source before citing it.

### Grade A -- Primary Law

Statutes, official gazettes, government portals. Highest reliability.

| Jurisdiction | Sources | Patterns |
|---|---|---|
| Korea (한국) | law.go.kr, 관보 | 법률 제N호, 대통령령 제N호, 국무총리령, 부령 |
| US | congress.gov, ecfr.gov, govinfo.gov | Public Law XXX-XXX, Federal Register, USC, CFR |
| EU | eur-lex.europa.eu, Official Journal | Directive (EU) YYYY/NNN, Regulation (EU) YYYY/NNN, EUR-Lex CELEX |
| General | Official Gazette, Government Gazette | Any official government publication of law |

### Grade B -- Authoritative Secondary

Court decisions, regulatory interpretations, established law firm analysis.

- 대법원 판례, 헌재 결정, 유권해석 (법제처/행정기관)
- US Supreme Court opinions, Circuit Court opinions, agency guidance letters
- CJEU judgments, AG opinions, Commission guidance documents
- Law firm client alerts from established firms (with named authors)

### Grade C -- Academic / Commentary

Law reviews, journal articles, treatises. Useful for context, not as sole authority.

- Patterns: Abstract, References, KCI, RISS, SSRN, Law Review, Journal of...
- Treatises and legal encyclopedias (e.g., Am. Jur., Corpus Juris)
- Conference papers, working papers

### Grade D -- Unreliable

Never cite as sole basis for any legal claim.

- Wikipedia, 나무위키, unattributed blogs
- Social media posts, forum answers
- Any source without identifiable author or institutional backing

**Rule**: If a claim can only be supported by Grade D, tag it `[Unverified]` and note the limitation.

## Comparative Framework (10-Axis Matrix)

When comparing laws across jurisdictions, structure the analysis using these 10 axes.

### Matrix Template

| Axis | Jurisdiction A | Jurisdiction B | Divergence |
|---|---|---|---|
| 1. Regulatory Scope | | | |
| 2. Obligated Parties | | | |
| 3. Key Obligations | | | |
| 4. Exemptions and Safe Harbors | | | |
| 5. Sanctions and Penalties | | | |
| 6. Competent Authority | | | |
| 7. Effective Date and Transition | | | |
| 8. Extraterritorial Reach | | | |
| 9. Cross-Border Mechanisms | | | |
| 10. Pending Reforms | | | |

### Axis Definitions

1. **Regulatory Scope** -- what activities, sectors, or subject matter the law covers.
2. **Obligated Parties** -- who must comply (natural persons, legal entities, size thresholds).
3. **Key Obligations** -- the core duties imposed (reporting, consent, registration, etc.).
4. **Exemptions and Safe Harbors** -- carved-out categories, de minimis thresholds, safe harbor conditions.
5. **Sanctions and Penalties** -- civil fines, criminal penalties, administrative sanctions, ranges.
6. **Competent Authority** -- which body enforces, investigates, adjudicates.
7. **Effective Date and Transition** -- enactment date, grace periods, phased rollout.
8. **Extraterritorial Reach** -- whether the law applies to conduct outside its borders and under what conditions.
9. **Cross-Border Mechanisms** -- mutual recognition, adequacy decisions, treaties, bilateral agreements.
10. **Pending Reforms** -- bills in progress, announced regulatory initiatives, scheduled reviews.

### Divergence Commentary Rules

For each axis where jurisdictions differ materially:
- State the specific divergence in concrete terms (not "different approach").
- Identify which jurisdiction is stricter or broader.
- Note practical impact for a party operating in both jurisdictions.
- Flag pending reforms that may narrow or widen the gap.

## Counter-Analysis Checklist

Apply these 6 dimensions to key conclusions before finalizing output.

### 1. Alternative Interpretation

Could the statute or regulation be read differently? Check:
- Plain meaning vs. purposive interpretation
- Legislative history or preparatory works that suggest a different intent
- Conflicting agency guidance

### 2. Minority / Dissenting View

Are there dissenting opinions, minority academic positions, or split circuits/courts? Note them explicitly even if the majority view is clear.

### 3. Jurisdictional Risk

Does the conclusion depend on which jurisdiction's law applies? Flag choice-of-law issues, conflicts rules, and forum selection risks.

### 4. Factual Sensitivity

Would small changes in the facts reverse the conclusion? Identify the factual assumptions that the analysis depends on.

### 5. Practical Enforcement Risk

Is the law actively enforced? Check:
- Recent enforcement actions or prosecutions
- Regulatory capacity and priorities
- Industry practice vs. strict letter of law

### 6. Similar-Statute Confusion

Especially relevant for Korean law (법률 명칭 혼동):
- 개인정보 보호법 vs. 정보통신망법 vs. 신용정보법 -- overlapping data protection regimes
- 독점규제법 vs. 하도급법 vs. 대규모유통업법 -- competition/fair trade overlap
- 자본시장법 vs. 유사수신행위법 -- financial regulation overlap

Always verify you are citing the correct statute. Cross-check the statute's 소관부처 (competent ministry) and 법령체계 (delegation hierarchy).

## Quality Gate

Every research output must pass all of these before delivery:

- [ ] Every legal claim has a pinpoint citation (article, section, paragraph) to a source graded A or B
- [ ] No claim relies solely on Grade D sources
- [ ] Unverified findings are tagged `[Unverified]`
- [ ] Counter-analysis applied to key conclusions (at minimum dimensions 1, 3, and 4)
- [ ] Jurisdiction clearly identified for every cited provision
- [ ] For comparative analysis: 10-axis matrix completed for all compared jurisdictions
- [ ] Temporal scope stated -- law as of which date

If a gate fails, fix it before producing output. If it cannot be fixed (e.g., no Grade A/B source exists), state the limitation explicitly.

## Cross-Reference to Jurisdiction-Specific Skills

Route to these skills when the query fits a single jurisdiction:

| Jurisdiction | Skill | Capabilities |
|---|---|---|
| Korea (한국) | `/korean-law-research` | 64 tools via public-data-worker -- 법령, 판례, 해석례, 행정규칙, 자치법규, 조문연혁, 신구대조, 3단대조 |
| US | `/us-legal-research` | CourtListener (case law, dockets) + GovInfo (USC, CFR, Federal Register) |
| EU | `/eu-regulatory-compliance` | EUR-Lex directives, regulations, CJEU case law |
| Academic papers | `/academic-research` | arXiv + Semantic Scholar for law review articles and working papers |

When research spans multiple jurisdictions, call multiple skills in sequence and merge results using this skill's methodology.

## External Source Lookup

For jurisdictions without a dedicated skill, use `web_search` and `web_fetch` with these priority sources:

| Source | URL Pattern | Grade |
|---|---|---|
| WorldLII | www.worldlii.org | B |
| WIPO Lex | www.wipo.int/wipolex/ | A |
| ILO NATLEX | www.ilo.org/dyn/natlex/ | A |
| UN Treaty Collection | treaties.un.org | A |
| OECD iLibrary | www.oecd-ilibrary.org | B-C |

Always verify external sources against official government portals when available.
