export type SkillCategory =
  | "getting-started"
  | "research"
  | "legal"
  | "finance"
  | "accounting"
  | "crypto"
  | "marketing"
  | "productivity"
  | "business"
  | "store-management"
  | "memory"
  | "lifestyle";

export interface SkillDef {
  id: string;
  /** Slash command aliases (English + Korean + short forms). First entry is the primary display name. */
  commands?: string[];
  category: SkillCategory;
  related?: string[];
}

export type CatalogSkill = SkillDef;

export interface CategoryMeta {
  id: SkillCategory;
  label: string;
  color: string;
}

export const CATEGORIES: CategoryMeta[] = [
  { id: "getting-started", label: "Getting Started", color: "text-yellow-700 bg-yellow-50 border-yellow-200" },
  { id: "research", label: "Research", color: "text-blue-700 bg-blue-50 border-blue-200" },
  { id: "legal", label: "Legal", color: "text-indigo-700 bg-indigo-50 border-indigo-200" },
  { id: "finance", label: "Finance", color: "text-emerald-700 bg-emerald-50 border-emerald-200" },
  { id: "accounting", label: "Tax & Accounting", color: "text-teal-700 bg-teal-50 border-teal-200" },
  { id: "crypto", label: "Crypto", color: "text-violet-700 bg-violet-50 border-violet-200" },
  { id: "marketing", label: "Marketing", color: "text-orange-700 bg-orange-50 border-orange-200" },
  { id: "productivity", label: "Productivity", color: "text-cyan-700 bg-cyan-50 border-cyan-200" },
  { id: "business", label: "Business", color: "text-slate-700 bg-slate-50 border-slate-200" },
  { id: "store-management", label: "Store Management", color: "text-rose-700 bg-rose-50 border-rose-200" },
  { id: "memory", label: "Memory", color: "text-amber-700 bg-amber-50 border-amber-200" },
  { id: "lifestyle", label: "Lifestyle", color: "text-pink-700 bg-pink-50 border-pink-200" },
];

/** Skills that cannot be disabled — system-critical */
export const CORE_SKILLS = new Set([
  "using-skills",
  "meta-cognition",
]);

// Purpose categories shown during onboarding (matching landing page)
export type PurposeCategory =
  | "finance"
  | "legal"
  | "accounting"
  | "tax"
  | "restaurants"
  | "sales"
  | "assistant"
  | "general";

export interface PurposeMeta {
  id: PurposeCategory;
  label: string;
  emoji: string;
  descriptionKey: string;
}

export const PURPOSE_OPTIONS: PurposeMeta[] = [
  { id: "finance", label: "purposeFinance", emoji: "📈", descriptionKey: "purposeFinanceDesc" },
  { id: "legal", label: "purposeLegal", emoji: "⚖️", descriptionKey: "purposeLegalDesc" },
  { id: "accounting", label: "purposeAccounting", emoji: "📊", descriptionKey: "purposeAccountingDesc" },
  { id: "tax", label: "purposeTax", emoji: "🧾", descriptionKey: "purposeTaxDesc" },
  { id: "restaurants", label: "purposeRestaurants", emoji: "🍽️", descriptionKey: "purposeRestaurantsDesc" },
  { id: "sales", label: "purposeSales", emoji: "💼", descriptionKey: "purposeSalesDesc" },
  { id: "assistant", label: "purposeAssistant", emoji: "🤖", descriptionKey: "purposeAssistantDesc" },
  { id: "general", label: "purposeGeneral", emoji: "🌐", descriptionKey: "purposeGeneralDesc" },
];

/**
 * Skills to DISABLE for each purpose category.
 * "general" and "assistant" enable everything.
 * CORE_SKILLS are never disabled regardless of purpose.
 */
export const PURPOSE_DISABLED_SKILLS: Record<PurposeCategory, string[]> = {
  finance: [
    "google-ads", "meta-ads", "ad-copywriter", "marketing-report", "shorts-studio",
    "meta-insights", "meta-social", "social-browser",
    "korean-life", "travel", "restaurant", "golf-caddie",
    "pos-sales", "pos-menu-strategy", "pos-accounting", "pos-inventory", "moltbook",
    "ethical-hacking-methodology",
  ],
  legal: [
    "trading", "court-auction", "polymarket", "korean-corporate-disclosure",
    "us-stock-search",
    "financial-statement-forensics", "capital-allocation-quality",
    "alpha-vantage-finance", "finnhub-market-data", "fmp-financial-data", "yahoo-finance-data",
    "fred-economic-data", "imf-economic-data", "world-bank-data", "sec-edgar-research", "yaml-finance-data",
    "kyberswap", "clober", "across-bridge", "crypto-market-data",
    "google-ads", "meta-ads", "ad-copywriter", "marketing-report", "shorts-studio",
    "meta-insights", "meta-social", "social-browser",
    "korean-life", "travel", "restaurant", "golf-caddie",
    "pos-sales", "pos-menu-strategy", "pos-accounting", "pos-inventory", "moltbook",
    "ethical-hacking-methodology",
  ],
  accounting: [
    // korean-corporate-disclosure stays ENABLED for accounting
    "trading", "polymarket",
    "kyberswap", "clober", "across-bridge", "crypto-market-data",
    "google-ads", "meta-ads", "ad-copywriter", "marketing-report", "shorts-studio",
    "meta-insights", "meta-social", "social-browser",
    "korean-life", "travel", "restaurant", "golf-caddie",
    "pos-sales", "pos-menu-strategy", "pos-accounting", "pos-inventory", "moltbook",
    "ethical-hacking-methodology",
  ],
  tax: [
    // korean-corporate-disclosure stays ENABLED for tax
    "trading", "polymarket",
    "kyberswap", "clober", "across-bridge", "crypto-market-data",
    "google-ads", "meta-ads", "ad-copywriter", "marketing-report", "shorts-studio",
    "meta-insights", "meta-social", "social-browser",
    "korean-life", "travel", "restaurant", "golf-caddie",
    "pos-sales", "pos-menu-strategy", "pos-accounting", "pos-inventory", "moltbook",
    "ethical-hacking-methodology",
  ],
  restaurants: [
    // pos-* and business-crm, personal-crm are REQUIRED for restaurants (always enabled)
    "trading", "court-auction", "polymarket", "korean-corporate-disclosure",
    "us-stock-search",
    "financial-statement-forensics", "capital-allocation-quality",
    "alpha-vantage-finance", "finnhub-market-data", "fmp-financial-data", "yahoo-finance-data",
    "fred-economic-data", "imf-economic-data", "world-bank-data", "sec-edgar-research", "yaml-finance-data",
    "kyberswap", "clober", "across-bridge", "crypto-market-data",
    "general-legal-research", "korean-law-research", "tax-regulation-research", "us-legal-research",
    "eu-regulatory-compliance", "academic-research",
    "ethical-hacking-methodology",
    "moltbook",
  ],
  sales: [
    "trading", "court-auction", "polymarket", "korean-corporate-disclosure",
    "us-stock-search",
    "financial-statement-forensics", "capital-allocation-quality",
    "alpha-vantage-finance", "finnhub-market-data", "fmp-financial-data", "yahoo-finance-data",
    "fred-economic-data", "imf-economic-data", "world-bank-data", "sec-edgar-research", "yaml-finance-data",
    "kyberswap", "clober", "across-bridge", "crypto-market-data",
    "general-legal-research", "korean-law-research", "tax-regulation-research", "us-legal-research",
    "eu-regulatory-compliance",
    "korean-life", "travel", "restaurant", "golf-caddie",
    "pos-sales", "pos-menu-strategy", "pos-accounting", "pos-inventory", "moltbook",
    "ethical-hacking-methodology",
  ],
  assistant: [],
  general: [],
};

export const SKILLS: SkillDef[] = [
  // Getting Started
  { id: "user-guide", commands: ["guide", "가이드"], category: "getting-started", related: ["using-skills", "task-pipeline"] },
  { id: "using-skills", category: "getting-started", related: ["user-guide", "task-pipeline"] },
  { id: "task-pipeline", commands: ["bulk", "대량", "일괄"], category: "getting-started", related: ["user-guide", "using-skills", "pipeline", "plan"] },
  { id: "plan", commands: ["plan", "계획"], category: "getting-started", related: ["task-pipeline", "pipeline", "complex-coding"] },
  { id: "pipeline", commands: ["pipeline", "파이프라인"], category: "getting-started", related: ["task-pipeline", "deep-research-loop", "plan"] },
  // Research
  { id: "deep-research", commands: ["research", "연구", "조사", "리서치"], category: "research", related: ["deep-research-loop", "web-search"] },
  { id: "deep-research-loop", commands: ["research-loop", "반복연구"], category: "research", related: ["deep-research"] },
  { id: "general-legal-research", commands: ["comparative-law", "법률연구", "비교법"], category: "legal", related: ["korean-law-research", "us-legal-research", "eu-regulatory-compliance"] },
  { id: "korean-law-research", commands: ["korean-law", "한국법", "법령", "판례"], category: "legal", related: ["general-legal-research", "tax-regulation-research", "us-legal-research", "legal-document-drafter"] },
  { id: "legal-document-drafter", commands: ["법률문서", "계약서작성", "내용증명", "합의서", "고소장", "legal-doc"], category: "legal", related: ["korean-law-research", "general-legal-research", "court-auction"] },
  { id: "tax-regulation-research", commands: ["tax", "세법", "세무"], category: "accounting", related: ["korean-law-research", "accounting"] },
  { id: "us-legal-research", commands: ["us-law", "미국법"], category: "legal", related: ["general-legal-research", "korean-law-research"] },
  { id: "eu-regulatory-compliance", commands: ["eu-law", "유럽법", "GDPR"], category: "legal", related: ["general-legal-research", "us-legal-research", "korean-law-research"] },
  // patent-ip-research: retired 2026-04-20 — upstream USPTO PatentsView API was deprecated.
  { id: "academic-research", commands: ["papers", "논문", "학술"], category: "research", related: ["deep-research"] },
  // Finance
  { id: "trading", commands: ["trading", "트레이딩", "매매", "주식"], category: "finance", related: ["crypto-market-data", "kyberswap"] },
  { id: "alpha-vantage-finance", commands: ["alpha-vantage", "주가데이터"], category: "finance", related: ["us-stock-search", "finnhub-market-data", "yahoo-finance-data"] },
  { id: "finnhub-market-data", commands: ["finnhub", "시장뉴스"], category: "finance", related: ["alpha-vantage-finance", "us-stock-search", "yahoo-finance-data"] },
  { id: "fmp-financial-data", commands: ["fmp", "재무데이터"], category: "finance", related: ["equity-financials", "us-stock-search", "sec-edgar-research"] },
  { id: "yahoo-finance-data", commands: ["yahoo-finance", "야후주식"], category: "finance", related: ["us-stock-search", "alpha-vantage-finance", "finnhub-market-data"] },
  { id: "fred-economic-data", commands: ["fred", "경제지표"], category: "finance", related: ["imf-economic-data", "world-bank-data"] },
  { id: "imf-economic-data", commands: ["imf", "국제경제"], category: "finance", related: ["fred-economic-data", "world-bank-data"] },
  { id: "world-bank-data", commands: ["world-bank", "세계은행"], category: "finance", related: ["fred-economic-data", "imf-economic-data"] },
  { id: "sec-edgar-research", commands: ["sec-edgar", "SEC공시"], category: "finance", related: ["us-stock-search", "equity-research", "fmp-financial-data"] },
  { id: "yaml-finance-data", commands: ["yaml-finance", "재무YAML", "로컬재무데이터"], category: "finance", related: ["financial-statements", "equity-financials", "excel-processing"] },
  { id: "court-auction", commands: ["auction", "경매", "공매"], category: "legal", related: ["korean-law-research"] },
  { id: "polymarket", commands: ["polymarket", "예측시장"], category: "finance", related: ["crypto-market-data"] },
  { id: "us-stock-search", commands: ["us-stock", "미국주식"], category: "finance", related: ["trading", "korean-corporate-disclosure"] },
  { id: "equity-research", commands: ["종목분석", "리서치리포트", "equity-research", "목표가"], category: "finance", related: ["equity-business", "equity-financials", "financial-statement-forensics", "capital-allocation-quality", "equity-industry", "equity-valuation", "korean-corporate-disclosure", "us-stock-search"] },
  { id: "equity-business", commands: ["사업분석", "business-analysis", "moat"], category: "finance", related: ["equity-research", "equity-financials"] },
  { id: "equity-financials", commands: ["재무분석", "financial-analysis", "ROIC", "FCF"], category: "finance", related: ["equity-research", "financial-statement-forensics", "capital-allocation-quality", "equity-valuation", "korean-corporate-disclosure", "financial-statements"] },
  { id: "financial-statement-forensics", commands: ["재무제표포렌식", "회계포렌식", "forensic-accounting", "QoE"], category: "accounting", related: ["equity-financials", "capital-allocation-quality", "accounting", "korean-corporate-disclosure", "sec-edgar-research"] },
  { id: "capital-allocation-quality", commands: ["자본배분", "주주환원분석", "capital-allocation", "owner-returns"], category: "finance", related: ["equity-financials", "financial-statement-forensics", "equity-valuation", "korean-corporate-disclosure", "sec-edgar-research"] },
  { id: "equity-industry", commands: ["산업분석", "industry-analysis"], category: "finance", related: ["equity-research", "korean-law-research", "us-legal-research"] },
  { id: "equity-valuation", commands: ["가치평가", "valuation", "DCF", "football-field"], category: "finance", related: ["equity-research", "equity-financials"] },
  // Accounting
  { id: "accounting", commands: ["accounting", "회계", "결산"], category: "accounting", related: ["cash-flow-statement", "audit-report-draft", "financial-statements", "financial-statement-forensics", "tax-regulation-research", "korean-corporate-disclosure"] },
  { id: "cash-flow-statement", commands: ["cash-flow", "현금흐름표"], category: "accounting", related: ["accounting", "financial-statements", "excel-processing"] },
  { id: "audit-report-draft", commands: ["audit-report", "감사보고서"], category: "accounting", related: ["accounting", "financial-statements", "document-writer"] },
  { id: "financial-statements", commands: ["financials", "재무제표"], category: "accounting", related: ["accounting", "cash-flow-statement", "audit-report-draft", "financial-statement-forensics", "excel-processing"] },
  { id: "korean-corporate-disclosure", commands: ["disclosure", "공시", "DART", "사업보고서"], category: "accounting", related: ["trading", "accounting", "financial-statement-forensics", "capital-allocation-quality", "tax-regulation-research"] },
  // Crypto
  { id: "kyberswap", commands: ["스왑", "swap"], category: "crypto", related: ["clober", "across-bridge"] },
  { id: "clober", commands: ["limit-order", "리밋오더"], category: "crypto", related: ["kyberswap", "across-bridge"] },
  { id: "across-bridge", commands: ["브릿지", "bridge"], category: "crypto", related: ["kyberswap", "clober"] },
  { id: "crypto-market-data", commands: ["코인시세", "crypto"], category: "crypto", related: ["trading", "kyberswap"] },
  // Marketing
  { id: "google-ads", commands: ["google-ads", "구글광고"], category: "marketing", related: ["meta-ads", "marketing-report"] },
  { id: "meta-ads", commands: ["meta-ads", "메타광고", "인스타광고"], category: "marketing", related: ["google-ads", "ad-copywriter"] },
  { id: "ad-copywriter", commands: ["ad-copy", "광고카피", "광고문구"], category: "marketing", related: ["google-ads", "meta-ads"] },
  { id: "marketing-report", commands: ["marketing-report", "마케팅보고서", "마케팅리포트"], category: "marketing", related: ["google-ads", "meta-ads"] },
  { id: "shorts-studio", commands: ["shorts", "쇼츠", "reels", "릴스"], category: "marketing", related: ["ad-creative-generator", "meta-social", "twitter", "ad-copywriter"] },
  { id: "meta-insights", commands: ["meta-insights", "페이스북분석", "인스타분석"], category: "marketing", related: ["meta-ads", "meta-social", "marketing-report"] },
  { id: "meta-social", commands: ["meta-social", "페이스북포스팅", "인스타포스팅"], category: "marketing", related: ["meta-ads", "meta-insights", "twitter"] },
  { id: "social-browser", commands: ["social-browser", "sns", "instagram", "x"], category: "marketing", related: ["meta-social", "twitter", "browser"] },
  // Productivity
  { id: "web-search", commands: ["search", "검색", "웹검색", "구글링"], category: "productivity", related: ["deep-research"] },
  { id: "jina-reader", commands: ["jina", "URL읽기", "기사읽기"], category: "productivity", related: ["web-search", "insane-fetch", "deep-research"] },
  { id: "insane-fetch", commands: ["insane-search", "난공URL", "WAF우회"], category: "productivity", related: ["jina-reader", "web-search", "deep-research"] },
  { id: "browser", commands: ["browser", "브라우저"], category: "productivity", related: ["web-search", "firecrawl", "insane-fetch"] },
  { id: "firecrawl", commands: ["firecrawl", "크롤링", "스크래핑"], category: "productivity", related: ["web-search", "browser", "jina-reader"] },
  { id: "visualization", commands: ["chart", "차트", "그래프", "시각화"], category: "productivity", related: ["excel-processing", "document-writer"] },
  { id: "github", commands: ["github", "PR", "풀리퀘"], category: "productivity", related: ["complex-coding"] },
  { id: "google-calendar", commands: ["calendar", "캘린더", "일정"], category: "productivity", related: ["google-gmail", "google-docs"] },
  { id: "google-docs", commands: ["docs", "구글독스"], category: "productivity", related: ["google-drive", "google-sheets", "document-writer"] },
  { id: "google-drive", commands: ["drive", "구글드라이브"], category: "productivity", related: ["google-docs", "google-sheets"] },
  { id: "google-gmail", commands: ["gmail", "이메일"], category: "productivity", related: ["google-calendar", "slack-integration"] },
  { id: "google-sheets", commands: ["sheets", "구글시트"], category: "productivity", related: ["google-docs", "google-drive", "excel-processing"] },
  { id: "slack-integration", commands: ["slack", "슬랙"], category: "productivity", related: ["google-gmail", "notion-integration"] },
  { id: "notion-integration", commands: ["notion", "노션"], category: "productivity", related: ["notion-kb", "google-docs", "knowledge-search"] },
  { id: "notion-kb", commands: ["notion-kb", "노션KB", "노션동기화"], category: "productivity", related: ["notion-integration", "knowledge-search", "knowledge-write"] },
  { id: "twitter", commands: ["twitter", "트위터", "X포스팅"], category: "productivity", related: ["meta-social"] },
  { id: "spotify-integration", commands: ["spotify", "스포티파이", "음악"], category: "productivity", related: [] },
  { id: "zapier", commands: ["zapier", "재피어", "자동화"], category: "productivity", related: ["google-gmail", "slack-integration", "notion-integration"] },
  { id: "ethical-hacking-methodology", commands: ["pentest", "모의해킹", "보안점검"], category: "productivity", related: [] },
  { id: "excel-processing", commands: ["excel", "엑셀"], category: "productivity", related: ["hwpx", "document-reader"] },
  { id: "hwpx", commands: ["한글", "hwp"], category: "productivity", related: ["excel-processing", "document-reader"] },
  { id: "document-reader", commands: ["read-doc", "읽기", "문서읽기"], category: "productivity", related: ["excel-processing", "document-writer", "hwpx"] },
  { id: "document-writer", commands: ["write-doc", "문서작성", "보고서"], category: "productivity", related: ["document-reader", "excel-processing", "hwpx"] },
  { id: "deepl-translation", commands: ["translate", "번역"], category: "productivity", related: ["web-search"] },
  { id: "writing-style", commands: ["writing-style", "글쓰기", "문체"], category: "productivity", related: ["deepl-translation", "document-reader", "humanize-korean"] },
  { id: "humanize-korean", commands: ["humanize", "윤문", "AI티제거", "번역투제거", "사람처럼"], category: "productivity", related: ["writing-style", "document-writer", "deepl-translation"] },
  { id: "deep-analysis", commands: ["분석", "analyze", "요약", "비교"], category: "research", related: ["knowledge-search", "document-reader", "deep-research", "excel-processing"] },
  { id: "knowledge-search", commands: ["kb", "지식검색", "지식베이스", "knowledge-base"], category: "productivity", related: ["knowledge-write", "document-reader", "qmd-search", "deep-research", "deep-analysis"] },
  { id: "knowledge-write", commands: ["kb-write", "지식저장", "메모저장", "knowledge-base-write"], category: "productivity", related: ["knowledge-search", "document-reader"] },
  { id: "complex-coding", commands: ["coding", "코딩"], category: "productivity", related: ["document-reader", "document-writer"] },
  { id: "loop", commands: ["loop", "루프", "반복", "주기"], category: "productivity", related: ["pipeline", "task-pipeline", "deep-research-loop"] },
  { id: "moltbook", commands: ["moltbook", "몰트북"], category: "productivity" },
  // Superpowers (bundled dev-discipline skill suite — core-agent 0.16.0+)
  { id: "brainstorming", commands: ["brainstorm", "브레인스토밍"], category: "productivity", related: ["writing-plans", "plan"] },
  { id: "systematic-debugging", commands: ["debug", "디버깅"], category: "productivity", related: ["verification-before-completion", "test-driven-development"] },
  { id: "test-driven-development", commands: ["tdd"], category: "productivity", related: ["systematic-debugging", "verification-before-completion"] },
  { id: "writing-plans", commands: ["write-plan", "계획작성"], category: "productivity", related: ["plan", "executing-plans", "brainstorming"] },
  { id: "executing-plans", commands: ["execute-plan", "계획실행"], category: "productivity", related: ["writing-plans", "plan"] },
  { id: "writing-skills", commands: ["write-skill", "스킬작성"], category: "productivity", related: ["using-superpowers"] },
  { id: "using-superpowers", commands: ["superpowers"], category: "productivity", related: ["brainstorming", "writing-plans", "systematic-debugging"] },
  { id: "verification-before-completion", commands: ["verify"], category: "productivity", related: ["systematic-debugging", "test-driven-development"] },
  { id: "subagent-driven-development", commands: ["subagent-dev"], category: "productivity", related: ["dispatching-parallel-agents"] },
  { id: "dispatching-parallel-agents", commands: ["parallel-agents", "병렬에이전트"], category: "productivity", related: ["subagent-driven-development", "pipeline"] },
  { id: "using-git-worktrees", commands: ["worktree"], category: "productivity", related: ["subagent-driven-development", "finishing-a-development-branch"] },
  { id: "requesting-code-review", commands: ["request-review", "코드리뷰요청"], category: "productivity", related: ["receiving-code-review", "finishing-a-development-branch"] },
  { id: "receiving-code-review", commands: ["receive-review"], category: "productivity", related: ["requesting-code-review"] },
  { id: "finishing-a-development-branch", commands: ["finish-branch", "브랜치정리"], category: "productivity", related: ["requesting-code-review", "using-git-worktrees"] },
  // Onboarding (slash command — helps first-time users set up)
  { id: "onboarding", commands: ["onboarding", "온보딩", "시작하기"], category: "getting-started", related: ["user-guide", "using-skills"] },
  // Business
  { id: "personal-crm", commands: ["crm", "고객관리", "연락처"], category: "business", related: ["business-crm"] },
  { id: "business-crm", commands: ["business-crm", "비즈니스CRM", "거래처"], category: "business", related: ["personal-crm", "pos-sales"] },
  // Store Management (Toss Place POS)
  { id: "pos-sales", commands: ["sales", "매출", "주문현황"], category: "store-management", related: ["pos-menu-strategy", "pos-accounting", "business-crm"] },
  { id: "pos-menu-strategy", commands: ["menu", "메뉴분석", "인기메뉴"], category: "store-management", related: ["pos-sales", "pos-inventory"] },
  { id: "pos-accounting", commands: ["closing", "정산", "마감"], category: "store-management", related: ["pos-sales", "pos-inventory"] },
  { id: "pos-inventory", commands: ["inventory", "재고", "발주"], category: "store-management", related: ["pos-menu-strategy", "pos-sales"] },
  // Memory
  { id: "hipocampus-compaction", category: "memory", related: ["qmd-search"] },
  { id: "qmd-search", commands: ["memory-search", "기억검색", "메모리"], category: "memory", related: ["hipocampus-compaction"] },
  { id: "meta-cognition", category: "memory" },
  { id: "frustration-resolution", category: "memory", related: ["meta-cognition", "user-guide"] },
  // Lifestyle
  { id: "korean-life", commands: ["korean-life", "생활", "다이소", "편의점"], category: "lifestyle", related: ["restaurant"] },
  { id: "travel", commands: ["travel", "여행", "호텔", "항공"], category: "lifestyle", related: ["restaurant", "maps-google"] },
  { id: "restaurant", commands: ["restaurant", "맛집", "레스토랑", "미슐랭"], category: "lifestyle", related: ["korean-life", "travel"] },
  { id: "golf-caddie", commands: ["golf", "골프"], category: "lifestyle" },
  { id: "naver-realestate", commands: ["naver-realestate-search", "부동산", "아파트호가", "단지시세"], category: "lifestyle", related: ["korean-life", "court-auction"] },
  { id: "elevenlabs-tts", commands: ["tts", "음성합성"], category: "lifestyle", related: ["groq-stt"] },
  { id: "groq-stt", commands: ["stt", "음성인식"], category: "lifestyle", related: ["elevenlabs-tts"] },
  { id: "maps-google", commands: ["maps", "지도", "길찾기"], category: "lifestyle", related: ["maps-korea", "travel", "restaurant"] },
  { id: "maps-korea", commands: ["korea-map", "한국지도", "네이버지도"], category: "lifestyle", related: ["maps-google", "korean-life"] },
];
