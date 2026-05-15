# Useful MCP Servers

> Claude Code / Claude Desktop 에서 사용할 수 있는 유용한 MCP 서버 목록.
> 포맷: **Service** | Description | When to Use | Ref

---

## Search & Web

| Service | Description | When to Use | Ref |
|---------|-------------|-------------|-----|
| **Firecrawl** | 웹 스크래핑, 크롤링, 검색, 구조화 데이터 추출. 배치 처리 + 자동 재시도 지원 | 웹페이지 내용 추출, 사이트 전체 크롤링, 에이전트 기반 리서치가 필요할 때 | `npx -y firecrawl-mcp` / [github.com/firecrawl/firecrawl-mcp-server](https://github.com/firecrawl/firecrawl-mcp-server) |
| **Tavily** | AI 에이전트 전용 검색엔진. 실시간 웹 검색 + 페이지 추출 + 사이트 맵핑 + 크롤링 | 실시간 웹 검색, 특정 페이지에서 정보 추출, 사이트 구조 파악이 필요할 때 | `npx -y tavily-mcp@latest` / [github.com/tavily-ai/tavily-mcp](https://github.com/tavily-ai/tavily-mcp) |
| **Perplexity** | Sonar 모델 기반 웹 검색, 대화형 질의, 딥 리서치, 추론 기능 제공 | 실시간 정보가 필요한 질문, 깊이 있는 리서치, 추론이 필요한 검색 | `npx -y @perplexity-ai/mcp-server` / [github.com/perplexityai/modelcontextprotocol](https://github.com/perplexityai/modelcontextprotocol) |

---

## Memory & Knowledge

| Service | Description | When to Use | Ref |
|---------|-------------|-------------|-----|
| **Memento Protocol** | AI 에이전트용 영구 메모리. 의미 기반 회상, 자동 통합, skip list, 메모리 decay 지원 | 세션 간 컨텍스트 유지, 이전 시도/실패 기록, 장기 프로젝트 메모리가 필요할 때 | `npx memento-mcp init` / [github.com/myrakrusemark/memento-protocol](https://github.com/myrakrusemark/memento-protocol) |
| **Memstate AI** | 커스텀 LLM으로 구조화된 사실 추출 + 충돌 해결. 80% 토큰 압축률 | RAG/Graph 대비 효율적인 메모리 관리, 사실 기반 기억 저장이 필요할 때 | [memstate.ai](https://memstate.ai) |
| **Vectorize** | 벡터 검색 + 문서 추출(PDF->Markdown) + 파이프라인 데이터 기반 딥 리서치 | 문서 인덱싱 후 시맨틱 검색, PDF 텍스트 추출, 프라이빗 데이터 리서치가 필요할 때 | `npx -y @vectorize-io/vectorize-mcp-server@latest` / [github.com/vectorize-io/vectorize-mcp-server](https://github.com/vectorize-io/vectorize-mcp-server) |

---

## Database & Backend

| Service | Description | When to Use | Ref |
|---------|-------------|-------------|-----|
| **Supabase** | Supabase 플랫폼 통합. DB 관리, Auth, Edge Functions, Storage, 실시간 구독 | Supabase 프로젝트 DB 스키마 관리, Auth 설정, Edge Function 배포가 필요할 때 | HTTP: `https://mcp.supabase.com/mcp` / [github.com/supabase-community/supabase-mcp](https://github.com/supabase-community/supabase-mcp) |

---

## Cloud - AWS

| Service | Description | When to Use | Ref |
|---------|-------------|-------------|-----|
| **AWS Core** | AWS MCP 서버 통합 프록시. 역할 기반 환경변수로 15+ 서버 자동 구성 | AWS 솔루션 설계, 멀티 서비스 통합, 역할별 AWS 서버 일괄 활성화가 필요할 때 | `uvx awslabs.core-mcp-server@latest` / [github.com/awslabs/mcp](https://github.com/awslabs/mcp/tree/main/src/core-mcp-server) |
| **AWS CDK** | CDK 베스트 프랙티스, IaC 패턴, CDK Nag 보안 스캔, Solutions Constructs 가이드 | CDK로 인프라 구축, 보안 컴플라이언스 체크, Bedrock Agent 스키마 생성이 필요할 때 | `uvx awslabs.cdk-mcp-server@latest` / [github.com/awslabs/mcp](https://github.com/awslabs/mcp/tree/main/src/cdk-mcp-server) *(deprecated -> AWS IaC MCP Server)* |
| **AWS CloudTrail** | CloudTrail 이벤트 조회 + Lake SQL 쿼리. 보안 조사, 감사, 운영 트러블슈팅 | AWS 계정 활동 조사, 보안 이벤트 분석, API 호출 패턴 모니터링이 필요할 때 | `uvx awslabs.cloudtrail-mcp-server@latest` / [github.com/awslabs/mcp](https://github.com/awslabs/mcp/tree/main/src/cloudtrail-mcp-server) |

---

## Cloud - Google

| Service | Description | When to Use | Ref |
|---------|-------------|-------------|-----|
| **Google MCP** | Google 공식 MCP 서버 컬렉션. Maps, BigQuery, GKE, Workspace, Firebase 등 포함 | Google Cloud 서비스 통합, Workspace(Docs/Sheets/Gmail) 연동, Firebase 작업이 필요할 때 | [github.com/google/mcp](https://github.com/google/mcp) |
| **Google Cloud Run** | Cloud Run 서비스 배포, 조회, 로그 확인. 자연어 프롬프트로 배포 가능 | Cloud Run에 앱 배포, 서비스 상태 확인, 배포 로그 조회가 필요할 때 | `npx -y @google-cloud/cloud-run-mcp` / [github.com/GoogleCloudPlatform/cloud-run-mcp](https://github.com/GoogleCloudPlatform/cloud-run-mcp) |
| **Google Cloud (gcloud)** | gcloud CLI를 자연어로 조작. 로깅, 메트릭, 스토리지, IAM 통합 | GCP 리소스 관리, 복잡한 멀티스텝 클라우드 워크플로우 자동화가 필요할 때 | `npx -y @google-cloud/gcloud-mcp` / [github.com/googleapis/gcloud-mcp](https://github.com/googleapis/gcloud-mcp) |

---

## Cloud - Deploy & Infra

| Service | Description | When to Use | Ref |
|---------|-------------|-------------|-----|
| **Railway** | Railway 프로젝트, 서비스, 환경, 배포 관리. AI를 통한 인프라 제어 | Railway 서비스 배포/관리, 환경 변수 설정, 배포 로그 확인이 필요할 때 | `npx add-mcp @railway/mcp-server` / [github.com/railwayapp/railway-mcp-server](https://github.com/railwayapp/railway-mcp-server) |
| **Vercel** | Vercel 프로젝트 관리, 배포, 도메인, 로그, 환경 변수 제어 | Vercel 배포 관리, 프로젝트 설정, 로그 조회가 필요할 때 | HTTP: `https://mcp.vercel.com` / [mcp.vercel.com](https://mcp.vercel.com) |
| **RunPod** | RunPod GPU 인프라 관리. Pod, Endpoint, Template, Network Volume 제어 | GPU Pod 생성/관리, Serverless Endpoint 배포, 템플릿 설정이 필요할 때 | `npx @runpod/mcp-server@latest` / [github.com/runpod/runpod-mcp](https://github.com/runpod/runpod-mcp) |

---

## Payments & Commerce

| Service | Description | When to Use | Ref |
|---------|-------------|-------------|-----|
| **Stripe** | Stripe API 통합. 결제, 청구, 구독, 연결 계정 관리. OAuth + RAK 지원 | 결제 처리, 구독 관리, 청구서 생성, Stripe 대시보드 데이터 조회가 필요할 때 | `npx -y @stripe/mcp --api-key=KEY` / [github.com/stripe/agent-toolkit](https://github.com/stripe/agent-toolkit) |
| **PayPal** | PayPal API 통합. 인보이스, 결제, 환불, 구독, 분쟁 관리, 배송 추적 | PayPal 인보이스 생성/발송, 결제 처리, 구독 관리, 비즈니스 분석이 필요할 때 | `npm install @paypal/agent-toolkit` / [github.com/paypal/agent-toolkit](https://github.com/paypal/agent-toolkit) |
| **Bitly** | 링크 단축, QR 코드 생성, 링크 관리 및 분석 | 단축 URL 생성, QR 코드 생성, 링크 클릭 분석이 필요할 때 | [dev.bitly.com/bitly-mcp](https://dev.bitly.com/bitly-mcp/overview/quickstart/) |

---

## Blockchain & Web3

| Service | Description | When to Use | Ref |
|---------|-------------|-------------|-----|
| **Bankless Onchain** | 온체인 데이터 조회. ERC20 토큰, 트랜잭션 히스토리, 스마트 컨트랙트 상태, ABI 조회 | 블록체인 데이터 조회, 컨트랙트 상태 확인, 트랜잭션 분석이 필요할 때 | `npx @bankless/onchain-mcp` / [github.com/bankless/onchain-mcp](https://github.com/bankless/onchain-mcp) |
| **Clober** | 온체인 오더북 DEX. 리밋 오더, 마켓 메이킹, 오더북 조회 (Base, Arbitrum 등) | Clober DEX에서 주문 생성, 오더북 분석, LP 전략 실행이 필요할 때 | [github.com/clober-dex/mcp-server](https://github.com/clober-dex/mcp-server) |
| **Odos** | DEX 애그리게이터. DeFi 트레이딩 데이터, 스왑 견적, 포트폴리오 분석, 트랜잭션 히스토리 | 최적 스왑 경로 탐색, DeFi 포트폴리오 분석, 트레이딩 데이터 조회가 필요할 때 | [github.com/odos-xyz/odos-mcp](https://github.com/odos-xyz/odos-mcp) |
| **0x** | DEX 애그리게이터. 멀티체인 스왑 견적, 가스비 최적화, 토큰 가격 조회 | EVM 체인 스왑 견적/실행, 토큰 가격 비교, DEX 유동성 조회가 필요할 때 | HTTP: `https://docs.0x.org/_mcp/server` / [docs.0x.org](https://docs.0x.org/_mcp/server) |
| **Jupiter** | Solana DEX 애그리게이터. Jupiter 문서, 코드 예시, API 레퍼런스 검색 | Solana 토큰 스왑, Jupiter API 연동, Solana DeFi 개발이 필요할 때 | `claude mcp add --transport http jupiter https://dev.jup.ag/mcp` / [dev.jup.ag/mcp](https://dev.jup.ag/mcp) |

---

## Developer Tools

| Service | Description | When to Use | Ref |
|---------|-------------|-------------|-----|
| **Sentry** | Sentry 에러 트래킹 + 퍼포먼스 모니터링 연동. AI 기반 이벤트/이슈 검색 지원 | 프로덕션 에러 조사, 이슈 디버깅, 퍼포먼스 모니터링 데이터 조회가 필요할 때 | `npx @sentry/mcp-server@latest` / [github.com/getsentry/sentry-mcp](https://github.com/getsentry/sentry-mcp) |
| **JetBrains** | JetBrains IDE와 AI 어시스턴트 연동 프록시. IntelliJ 기반 IDE 전체 지원 | JetBrains IDE에서 코드 작업을 AI로 제어하고 싶을 때 | `npx -y @jetbrains/mcp-proxy` / [github.com/JetBrains/mcp-jetbrains](https://github.com/JetBrains/mcp-jetbrains) *(deprecated -> IDE 2025.2+ 내장 MCP 사용)* |

---

## Legal & Regulation (법률 & 규제)

| Service | Description | When to Use | Ref |
|---------|-------------|-------------|-----|
| **Korean Law (법제처)** | 한국 법령 검색. 법률/시행령/시행규칙 전문, 조문별 조회, 연혁 추적 | 한국 법률 조회, 특정 조문 확인, 법령 해석이 필요할 때 | `mcp-kr-legislation` / [law.go.kr Open API](https://www.law.go.kr/LSW/openApi.do) |
| **CourtListener** | 미국 판례 검색. 연방/주 법원 판결문, 구두 변론, 법률 인용 네트워크 | 미국 판례 검색, 법원 판결문 조회, 법적 선례 분석이 필요할 때 | `courtlistener-mcp` / [courtlistener.com/api](https://www.courtlistener.com/api/rest-info/) |
| **GovInfo** | 미국 연방 법률 문서. 연방법전(USC), 연방규정집(CFR), 의회법안, 연방관보 | 미국 연방법/규정 원문 조회, 입법 추적, 연방관보 검색이 필요할 때 | [api.govinfo.gov](https://api.govinfo.gov/docs/) |
| **EU Compliance** | EU 규제 데이터. GDPR, AI Act, Digital Markets Act 등 EU 지침/규정 조회 | EU 규제 준수 확인, GDPR 요구사항, EU 지침 원문 조회가 필요할 때 | `EU_compliance_MCP` / [EUR-Lex](https://eur-lex.europa.eu/) |

---

## Corporate Disclosure & Finance (기업공시 & 금융)

| Service | Description | When to Use | Ref |
|---------|-------------|-------------|-----|
| **OpenDART (금감원)** | 한국 기업 공시 조회. 사업보고서, 재무제표, 지분공시, 대량보유, 임원현황 | 한국 상장사 공시 조회, 재무제표 분석, 지분 구조 파악이 필요할 때 | `opendart-fss-mcp` / [opendart.fss.or.kr](https://opendart.fss.or.kr/) |
| **SEC EDGAR** | 미국 SEC 공시 조회. 10-K, 10-Q, 8-K, proxy statements, insider trading | 미국 상장사 공시 조회, SEC 파일링 검색, 재무 데이터 분석이 필요할 때 | `sec-edgar-mcp` / [efts.sec.gov](https://efts.sec.gov/LATEST/search-index?q=) |
| **GLEIF (LEI)** | 글로벌 법인식별기호(LEI) 조회. 기업 실체 확인, 소유구조, KYC 검증 | 기업 LEI 조회, 법인 실체 확인, 글로벌 KYC 검증이 필요할 때 | `gleif-mcp-server` / [api.gleif.org](https://api.gleif.org/api/v1) |

---

## Tax & IP (세금 & 지식재산)

| Service | Description | When to Use | Ref |
|---------|-------------|-------------|-----|
| **IRS (미국 국세청)** | 미국 세금 정보. Tax forms, publications, 세율표, EITC 계산기 | 미국 세금 규정 조회, IRS publication 참고, 세금 계산이 필요할 때 | `irs-taxpayer-mcp` / [irs.gov](https://www.irs.gov/) |
| **USPTO Patents** | 미국 특허/상표 검색. PatentsView 데이터, 특허 인용 네트워크, CPC 분류 | 특허 검색, 선행기술 조사, 특허 분석, 상표 조회가 필요할 때 | `patent_mcp_server` / [patentsview.org](https://patentsview.org/apis) |

---

## Language Learning (언어 학습)

| Service | Description | When to Use | Ref |
|---------|-------------|-------------|-----|
| **DeepL** | 고품질 번역 API. 36개 언어, 격식 제어, 용어집, 문서 번역 지원 | 텍스트/문서 번역, 다국어 콘텐츠 생성, 번역 품질이 중요할 때 | `deepl-mcp-server` / [api-free.deepl.com](https://www.deepl.com/docs-api) |
| **Free Dictionary** | 영어 사전 API. 정의, 발음, 예문, 품사, 유의어 | 영어 단어 뜻 조회, 발음 확인, 어휘 학습이 필요할 때 | [dictionaryapi.dev](https://dictionaryapi.dev/) |
| **ElevenLabs** | 최고 품질 AI TTS. 음성 복제, 다국어, 감정 제어 | 텍스트를 자연스러운 음성으로 변환, 발음 듣기, 다국어 음성 생성이 필요할 때 | `elevenlabs-mcp` / [elevenlabs.io/docs](https://elevenlabs.io/docs) |
| **Edge TTS** | Microsoft TTS. 다국어, 무료, API key 불필요 | 간단한 TTS, 발음 듣기가 필요할 때 (무료 대안) | `edge-tts-mcp` / edge-tts Python |

---

## Financial Market Data (금융 시장 데이터)

| Service | Description | When to Use | Ref |
|---------|-------------|-------------|-----|
| **Alpha Vantage** | 주가, 재무제표, 외환, 암호화폐, 기술지표, 경제 데이터 통합 API | 주식 시세/재무 분석, 외환/크립토 환율, 기술적 분석이 필요할 때 | `alpha-vantage-mcp` / [alphavantage.co](https://www.alphavantage.co/documentation/) |
| **Yahoo Finance** | 실시간 주가, 재무제표, 옵션, 애널리스트 추천. API key 불필요 | 빠른 주가 확인, 기본 재무 데이터 조회, key 없이 금융 데이터가 필요할 때 | `yahoo-finance-mcp` / yfinance |
| **FRED** | 미 연준 경제 데이터. GDP, 실업률, 인플레이션, 금리 등 80만+ 시계열 | 미국 경제 지표 조회, 매크로 분석, 경제 데이터 시계열이 필요할 때 | `fred-mcp-server` / [api.stlouisfed.org](https://fred.stlouisfed.org/docs/api/fred/) |
| **World Bank** | 글로벌 개발 지표. 200+ 국가, 1,000+ 경제/사회 지표 | 국가별 경제/사회 지표 비교, 글로벌 매크로 데이터가 필요할 때 | `world-bank-data-mcp` / [api.worldbank.org](https://datahelpdesk.worldbank.org/knowledgebase/topics/125589-developer-information) |
| **CoinCap** | 실시간 암호화폐 데이터. 시세, 시총, 거래소, 거래쌍. API key 불필요 | 크립토 시세 조회, 시장 분석, 실시간 가격 비교가 필요할 때 | `coincap-mcp` / [api.coincap.io](https://docs.coincap.io/) |
| **CoinGecko** | 18,000+ 코인, 1,000+ 거래소, 트렌딩, NFT/DeFi 데이터 | 포괄적 크립토 데이터, 트렌딩 코인, 메타데이터 조회가 필요할 때 | `coingecko-mcp` / [api.coingecko.com](https://docs.coingecko.com/) |
| **FMP** | 253+ 금융 도구. DCF, 비교평가, SEC 연동, 애널리스트 추정 | 포괄적 금융 분석, DCF 평가, 애널리스트 데이터가 필요할 때 | `fmp-mcp-server` / [financialmodelingprep.com](https://financialmodelingprep.com/developer/docs) |
| **Polygon.io** | 35+ 도구. 주식/옵션/외환/크립토. 실시간 호가, 뉴스, 배당 | 실시간 시장 데이터, 옵션 데이터, 분 단위 히스토리가 필요할 때 | `mcp_polygon` / [polygon.io](https://polygon.io/docs) |
| **Finnhub** | 실시간 주가, 기업 메트릭, 애널리스트 추천, 대안 데이터 | 실시간 호가, 애널리스트 추천, 기업 뉴스 조회가 필요할 때 | `mcp-finnhub` / [finnhub.io](https://finnhub.io/docs/api) |
| **IMF Data** | IMF 경제 데이터셋. CDIS, IFS, MFS 등. API key 불필요 | IMF 글로벌 경제 데이터, 국가 간 자본흐름 분석이 필요할 때 | `imf-data-mcp` / [datahelp.imf.org](https://datahelp.imf.org/knowledgebase/articles/667681-using-json-restful-web-service) |

---

## Spreadsheets (Built-in)

> Note: These capabilities are built into Open Magi — no MCP server needed.

| Feature | Description | When to Use |
|---------|-------------|-------------|
| **Excel Processing** | ExcelJS로 .xlsx 파일 생성/읽기/편집. 수식, 서식, 조건부서식 지원 | 다운로드 가능한 엑셀 파일 생성, 기존 .xlsx 파싱, 오프라인 스프레드시트가 필요할 때 |
| **Google Sheets** | Google Sheets API 연동. 읽기/쓰기/생성/서식/차트/공유 | 실시간 협업, 클라우드 스프레드시트, 차트/시각화, 링크 공유가 필요할 때 |

---

## Gaming

| Service | Description | When to Use | Ref |
|---------|-------------|-------------|-----|
| **OP.GG** | LoL, TFT, Valorant 실시간 게임 데이터. 챔피언 분석, e스포츠 일정, 메타 조합 | 게임 통계 조회, 챔피언/에이전트 분석, 메타 덱 추천, 리더보드 확인이 필요할 때 | Endpoint: `https://mcp-api.op.gg/mcp` / [github.com/opgginc/opgg-mcp](https://github.com/opgginc/opgg-mcp) |
