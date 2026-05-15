---
name: restaurant
description: Use when user asks about restaurants, dining, food recommendations, Michelin star restaurants, best places to eat, or food in a specific city/area. Covers Korea, Japan, and US markets.
---

# Restaurant Concierge

Find top-rated restaurants using Michelin Guide, Tabelog (Japan), and Google/Kakao Maps data.

## Data Sources (Priority Order)

1. **Michelin Guide** — Stars (1-3), Bib Gourmand, Green Star. Highest authority.
2. **Tabelog** — Japan's #1 restaurant platform. Score 3.5+ = excellent. 4.0+ = exceptional.
3. **Google Places** — Global coverage with user ratings and reviews.
4. **Kakao Places** — Korea-specific restaurant search (free).

## How to Search

### Step 1: Search Michelin + Tabelog (via restaurant-worker)

```
integration.sh "restaurant/search-restaurants" '{"city":"tokyo","country":"jp","cuisine":"sushi","limit":10}'
```

**Parameters (JSON body):**
- `city` — City name (e.g., "tokyo", "osaka", "seoul", "new-york")
- `country` — Country code: "jp" (Japan), "kr" (Korea), "us" (US)
- `cuisine` — Optional cuisine filter (e.g., "sushi", "ramen", "korean", "italian")
- `limit` — Max results (default: 20)

**Response:** Array of restaurants sorted by priority (Michelin first, then Tabelog by score).

### Step 2: Supplement with Google Places (if needed)

For more results or non-covered areas, use the maps-google skill:

```
integration.sh "maps/places/search?query=best+ramen+in+tokyo&language=en&type=restaurant"
```

### Step 3: Supplement with Kakao Places (Korea)

For Korean restaurant search, use the maps-korea skill:

```
integration.sh "maps-kr/kakao/places?query=강남역 맛집&size=10"
```

## Search by Source

### Michelin Only

```
integration.sh "restaurant/michelin-search" '{"city":"seoul","country":"kr","award":"star"}'
```

### Tabelog Only (Japan)

```
integration.sh "restaurant/tabelog-search" '{"prefecture":"tokyo","genre":"sushi","minScore":"4.0","limit":10}'
```

**Prefectures:** tokyo, osaka, kyoto, fukuoka, hokkaido, aichi, kanagawa, hyogo, hiroshima, miyagi, okinawa, nara, ishikawa, nagasaki, kumamoto

## Response Format

Present results with source badges and priority ordering:

```
🏅 미슐랭 2스타 | Sushi Saito
   ⭐ Michelin ★★ | 📍 도쿄 미나토구

🥇 타베로그 4.21 | 焼肉チャンピオン
   ⭐ Tabelog 4.21 | 💰 ¥8,000~ | 📍 도쿄 에비스
   "肉の質が本当に素晴らしい..."

📍 Google 4.7 (2,340) | Jungsik
   ⭐ Google 4.7 | 💰 $$$$ | 📍 NYC TriBeCa
```

## Guidelines

- Always search Michelin + Tabelog FIRST via restaurant-worker
- Use Google/Kakao to SUPPLEMENT, not replace curated sources
- For Japan: always include Tabelog results alongside Michelin
- For Korea: Michelin Seoul/Busan + Kakao/Google
- For US: Michelin + Google Places
- Show data source badges so user knows where info comes from
- Include budget/price range when available
- Mention review count for credibility context
