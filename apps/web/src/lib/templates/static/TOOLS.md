# Tools & Skills -- Meta-Layer Index

**라우팅 판단용 인덱스. 풀 API docs는 EXECUTION-TOOLS.md (서브에이전트에 전달).**

## Native Runtime Capabilities

문서/파일 생성과 전달은 가능한 한 runtime-native capability를 우선한다:
- `DocumentWrite` — `md` / `txt` / `html` / `pdf` / `docx` / `hwpx` 문서 생성·편집
- `SpreadsheetWrite` — `xlsx` / `csv` 등 스프레드시트 생성·편집
- `knowledge-search` / `KnowledgeSearch` — Personal/Org KB 컬렉션 검색, 문서 목록, manifest, converted markdown 조회
- `FileDeliver` — chat / KB / both 전달, retry/backoff 포함
- `SpawnAgent` — native subagent delegation. Use `model` only from the
  `SpawnAgent` tool schema enum, or omit `model` to inherit the bot's current
  configured runtime model. Do not invent model IDs or mix `agent-run.sh`
  provider-path examples into native `SpawnAgent` calls.

기존 파일이나 방금 생성된 artifact를 “파일로 줘/첨부해줘/다운로드”라고 요청하면 **새 문서를 만들지 말고** `FileDeliver`만 사용한다. workspace 경로가 있으면 `FileDeliver({ path: "..." })`, 생성 도구가 반환한 artifact가 있으면 `FileDeliver({ artifactId: "..." })`를 쓴다. `DocumentWrite`는 새 문서 생성, 포맷 변환, 명시적 편집 요청일 때만 선택한다.

`DocumentWrite`로 기존 마크다운/텍스트 파일을 DOCX/PDF 등으로 변환할 때는 `source: { type: "markdown", path: "report.md" }`도 가능하다. 구조화 JSON 블록 파일은 `source: { kind: "structured", blocksFile: "blocks.json" }`로 전달할 수 있다.

DOCX/HWPX는 가능한 경우 runtime agentic authoring loop를 사용한다. 특히 HWPX는 starter XML과 템플릿 header를 기반으로 작성하고 `validate.py` 및 source-content coverage guard로 검증하며, 기존 HWPX 편집은 reference 분석과 `page_guard.py`를 포함한다. 임의 스크립트보다 `DocumentWrite(format="hwpx")`를 우선한다.

스킬은 포맷별 작성 가이드와 도메인 지식을 제공하고, 실제 생성/전달 mechanics는 위 capability가 맡는다.

## Skill Mapping (domain -> skill)

서브에이전트에 "Read and follow `skills/<id>/SKILL.md`" 지시로 전달.

| Domain Keywords | Skill ID | Skill Path |
|----------------|----------|------------|
| 웹검색, 구글링, 찾아줘, search | web-search | skills/web-search/SKILL.md |
| URL내용, 기사읽기, 웹읽기, 마크다운추출 | jina-reader | skills/jina-reader/SKILL.md |
| 난공URL, WAF차단, 쿠팡, 링크드인, 에펨코리아 | insane-fetch | skills/insane-fetch/SKILL.md |
| 웹스크래핑, 크롤링, 사이트맵 | firecrawl | skills/firecrawl/SKILL.md |
| 심층연구, deep research, 조사 | deep-research | skills/deep-research/SKILL.md |
| 반복연구, 루프 리서치 | deep-research-loop | skills/deep-research-loop/SKILL.md |
| 논문, 학술, academic | academic-research | skills/academic-research/SKILL.md |
| 정확성, 근거, 출처, 최신성, hallucination 방지 | evidence-router | skills/evidence-router/SKILL.md |
| task_contract, acceptance criteria, verification_mode, 완료조건 | task-contract-orchestration | skills/task-contract-orchestration/SKILL.md |
| 완료검증, evidence before claims, fixed/pass 주장 | verification-before-completion | skills/verification-before-completion/SKILL.md |
| 에러, 실패, 버그, unexpected behavior | systematic-debugging | skills/systematic-debugging/SKILL.md |
| 재시도, 반복실패, blocked commit, failed fix | retry-with-strategy | skills/retry-with-strategy/SKILL.md |
| 위임결과 검수, subagent result, parallel task QC | subagent-result-qc | skills/subagent-result-qc/SKILL.md |
| 최종납품, 산출물 전달, 완료보고 | agentic-delivery-gate | skills/agentic-delivery-gate/SKILL.md |
| 파일첨부, 다운로드, 파일로 줘, attach file, export/download | file-send | skills/file-send/SKILL.md |
| 나중에 알려줘, 백그라운드, cron, 예약 알림 | async-work-monitoring | skills/async-work-monitoring/SKILL.md |
| 외부채널 전송, Telegram/Slack/Discord/email delivery | channel-delivery-safety | skills/channel-delivery-safety/SKILL.md |
| 한국법, 법령, 판례, 법률검색 | korean-law-research | skills/korean-law-research/SKILL.md |
| 세법, 세무, tax | tax-regulation-research | skills/tax-regulation-research/SKILL.md |
| 미국법, USC, CFR, 케이스로 | us-legal-research | skills/us-legal-research/SKILL.md |
| 주식, 트레이딩, 매매 | trading | skills/trading/SKILL.md |
| 법원경매, 공매, 경매물건 | court-auction | skills/court-auction/SKILL.md |
| 예측시장, polymarket | polymarket | skills/polymarket/SKILL.md |
| 미국주식, US stock | us-stock-search | skills/us-stock-search/SKILL.md |
| 회계, K-IFRS, 결산 | accounting | skills/accounting/SKILL.md |
| 현금흐름표 | cash-flow-statement | skills/cash-flow-statement/SKILL.md |
| 감사보고서 | audit-report-draft | skills/audit-report-draft/SKILL.md |
| 재무제표, 손익계산서, 대차대조표 | financial-statements | skills/financial-statements/SKILL.md |
| DART, 기업공시, 사업보고서 | korean-corporate-disclosure | skills/korean-corporate-disclosure/SKILL.md |
| 재무제표 포렌식, 이익품질, 회계 리스크, QoE | financial-statement-forensics | skills/financial-statement-forensics/SKILL.md |
| 자본배분, 주주환원, 비영업자산, CapEx 효율 | capital-allocation-quality | skills/capital-allocation-quality/SKILL.md |
| 스왑, DEX, KyberSwap | kyberswap | skills/kyberswap/SKILL.md |
| 리밋오더, CLOB, Clober | clober | skills/clober/SKILL.md |
| 브릿지, cross-chain, Across | across-bridge | skills/across-bridge/SKILL.md |
| 코인시세, 암호화폐, crypto price | crypto-market-data | skills/crypto-market-data/SKILL.md |
| Google Ads, 구글광고 | google-ads | skills/google-ads/SKILL.md |
| Meta Ads, 페이스북광고, 인스타광고 | meta-ads | skills/meta-ads/SKILL.md |
| 광고카피, 광고문구 | ad-copywriter | skills/ad-copywriter/SKILL.md |
| 마케팅보고서, 마케팅리포트 | marketing-report | skills/marketing-report/SKILL.md |
| 엑셀, xlsx, 스프레드시트 | excel-processing | skills/excel-processing/SKILL.md |
| hwp, hwpx, 한글문서 | hwpx | skills/hwpx/SKILL.md |
| PDF읽기, 문서읽기, DOCX | document-reader | skills/document-reader/SKILL.md |
| 문서작성, 보고서작성, 문서생성 | document-writer | skills/document-writer/SKILL.md |
| 번역, translation, deepl | deepl-translation | skills/deepl-translation/SKILL.md |
| 글쓰기스타일, writing style | writing-style | skills/writing-style/SKILL.md |
| 지식검색, KB검색, knowledge search | knowledge-search | skills/knowledge-search/SKILL.md |
| KB저장, KB쓰기, KB업로드, knowledge write, 지식저장 | knowledge-write | skills/knowledge-write/SKILL.md |
| 코딩, 프로그래밍, 개발 | complex-coding | skills/complex-coding/SKILL.md |
| CRM, 고객관리, 연락처 | personal-crm | skills/personal-crm/SKILL.md |
| 매장관리, POS, 토스플레이스 | tossplace-pos | skills/tossplace-pos/SKILL.md |
| 미슐랭, 타벨로그, 맛집, 레스토랑 | restaurant | skills/restaurant/SKILL.md |
| 호텔, 항공, 여행, airbnb | travel | skills/travel/SKILL.md |
| 골프, 골프장 | golf-caddie | skills/golf-caddie/SKILL.md |
| 부동산, 아파트, 호가, 단지, 매매시세 | naver-realestate | skills/naver-realestate/SKILL.md |
| 다이소, CU, CGV, 생활 | korean-life | skills/korean-life/SKILL.md |
| TTS, 음성합성, 목소리 | elevenlabs-tts | skills/elevenlabs-tts/SKILL.md |
| STT, 음성인식, 받아쓰기 | groq-stt | skills/groq-stt/SKILL.md |
| 상담녹음, 녹취, 변호사 상담, 세무 상담, 회계 상담, client call | consultation-transcript | skills/consultation-transcript/SKILL.md |
| 구글캘린더, 일정 | google-calendar | skills/google-calendar/SKILL.md |
| Gmail, 이메일 | google-gmail | skills/google-gmail/SKILL.md |
| Google Docs | google-docs | skills/google-docs/SKILL.md |
| Google Sheets | google-sheets | skills/google-sheets/SKILL.md |
| Google Drive, 드라이브 | google-drive | skills/google-drive/SKILL.md |
| 노션, Notion | notion-integration | skills/notion-integration/SKILL.md |
| 슬랙, Slack | slack-integration | skills/slack-integration/SKILL.md |
| 트위터, X, 트윗 | twitter | skills/twitter/SKILL.md |
| 디스코드, Discord | discord | (integration.sh discord/*) |
| 지도, 장소, 길찾기(한국) | maps-korea | skills/maps-korea/SKILL.md |
| 지도, 장소, 길찾기(해외) | maps-google | skills/maps-google/SKILL.md |
| 크론, 스케줄, 예약, 반복 | (see EXECUTION.md cron section) |
| 작업분할, 대량, bulk | task-pipeline | skills/task-pipeline/SKILL.md |
| 계획, plan, 작업계획 | plan | skills/plan/SKILL.md |
| 결제, x402, USDC | x402-payment | skills/x402-payment/SKILL.md |
| 이미지생성 | (integration.sh gemini-image/*) |
| 영상생성, 비디오 | (integration.sh gemini-video/*) |

## Service Categories (for EXECUTION-TOOLS.md routing)

| Category | Services | When |
|----------|----------|------|
| Web | web-search.sh, firecrawl.sh | 검색, 스크래핑 |
| Finance | fmp/*, dart/* | 주식, 공시 |
| Legal/Tax | law/*, tax/* | 법령, 세법 |
| Maps | maps-kr/*, maps/* | 지도 |
| Food | restaurant/* | 미슐랭, 맛집 |
| Travel | travel/* | 호텔, 항공 |
| Auction | auction/* | 경매 |
| Golf | golf/* | 골프 |
| Media | gemini-image/*, gemini-video/*, elevenlabs/* | 생성 |
| Consultation ASR | chat audio attachment pipeline | 상담 녹음 → transcript/memo/tasks + KB |
| Integrations | google/*, notion/*, slack/*, twitter/*, meta/* | OAuth |
| Knowledge | knowledge/* | KB |
| Browser | native Browser | JS 렌더링, 클릭, 폼 입력, 로그인 화면 확인 |
| DeFi | kyber-swap.sh, clober, across-bridge | 크립토 |
| Korean Life | korean-life/* | 생활 |
| LLM | llm/* | AI 모델 |
| Accounting | accounting/* | 회계기준, K-IFRS, XBRL |
| Documents | document-worker | 문서변환 |

## Slash Command Shortcuts → Skill (MANDATORY)

유저가 아래 slash 명령어 또는 키워드를 사용하면, **무조건 해당 스킬을 먼저 읽고 따라야 한다.**
파일시스템 검색, find, ls 등으로 직접 찾으려 하지 말 것.

| Slash / Keyword | Skill | Action |
|----------------|-------|--------|
| `/kb`, `KB검색`, `지식검색`, `knowledge base`, `내 파일`, `업로드한 문서` | knowledge-search | native `knowledge-search`/`KnowledgeSearch` 도구 사용. 필요 시 `skills/knowledge-search/SKILL.md`의 `kb-search.sh` 전략을 따른다 |
| `/kb-write`, `KB저장`, `KB에 저장`, `KB업로드`, `지식저장` | knowledge-write | 문서/파일 산출물은 `FileDeliver(target="kb")`, 순수 마크다운 노트는 `skills/knowledge-write/SKILL.md` |
| `/file`, `첨부`, `다운로드`, `파일로 줘`, `attach`, `download`, `export` | file-send | `skills/file-send/SKILL.md`를 읽고 native `FileDeliver(target=chat|both)`를 사용 |
| `/웹검색`, `검색해줘`, `구글링` | web-search | `skills/web-search/SKILL.md` |
| `브라우저`, `웹사이트 조작`, `클릭`, `로그인해서 확인`, `JS 렌더링`, `browser`, `click`, `fill form` | browser | native `Browser` 도구 우선. 상세 절차는 `skills/browser/SKILL.md` |
| `/심층연구`, `deep research` | deep-research | `skills/deep-research/SKILL.md` |

**핵심:**
- "KB에서 찾아줘", "/kb", "KB검색" → native `knowledge-search` / `KnowledgeSearch`
- "KB에 저장", "KB에 업로드", "KB쓰기" → `knowledge-write` 스킬
- "파일로 줘", "첨부해줘", "다운로드", "채팅에도 첨부" → `file-send` 스킬
- "브라우저로 열어봐", "로그인해서 확인", "클릭해서 진행", "JS 렌더링 필요" → native `Browser`
- 기존 파일 전달 요청을 HTML/DOCX “설명서” 생성 작업으로 확대하지 않는다.
- KB는 **읽기(search)와 쓰기(write) 모두 가능**. 절대로 workspace 파일시스템에서 find/ls로 찾지 말 것 — KB는 별도 API로만 접근 가능.
- 생성한 리포트/문서/표/이미지는 workspace 경로만 말하고 끝내지 말 것. native delivery evidence 없이 완료로 닫지 않는다.
- 비공개/로그인/대량 플랫폼 데이터는 브라우저 루프를 약속하지 말고, CSV/XLSX/export 또는 승인된 API/provider connector를 요청한다.

## Routing Rule
1. 유저 요청에서 domain keywords 또는 slash 명령어 매칭
2. 해당 skill path를 서브에이전트 프롬프트에 포함: "Read and follow `skills/<id>/SKILL.md`"
3. 서비스 호출이 필요하면 EXECUTION-TOOLS.md도 함께 전달
4. 매칭되는 skill이 없으면 서브에이전트가 직접 스킬 스캔하도록 지시
