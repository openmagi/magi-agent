---
name: travel
description: Search hotels (2M+ worldwide), Airbnb stays, and flights. Book hotels with payment links. Use when users ask about travel, accommodation, flights, or vacation planning.
---

# Travel — Hotels, Flights & Stays

Search hotels worldwide, find Airbnb stays, and search flights with real-time pricing.

## When to Use
- User asks about hotels, accommodation, or lodging
- User wants to book a hotel or find prices
- User asks about flights or airfare
- User wants to find Airbnb or vacation rentals
- Travel planning, trip comparison, destination discovery

## API Access

Platform service — free, no API key needed. All endpoints via `integration.sh`.

Response format: `{ "data": {...}, "synced_at": "..." }`

---

## Hotels (Jinko — 2M+ hotels worldwide)

### 1. Find Place (location → coordinates)

```
integration.sh "travel/hotels/find-place?query=Seoul&language=en"
integration.sh "travel/hotels/find-place?query=파리&language=ko"
```

Response: list of places with `latitude`, `longitude`, `name`

### 2. Search Hotels

```
integration.sh "travel/hotels/search?latitude=37.5665&longitude=126.978&check_in_date=2026-04-01&check_out_date=2026-04-03&adults=2"
```

Parameters:
- `latitude`, `longitude` (required) — from find-place
- `check_in_date`, `check_out_date` (YYYY-MM-DD)
- `adults` (default: 2), `children` (default: 0)
- `facilities` — comma-separated facility IDs (from get-facilities)

Response: up to 50 hotels with name, address, star rating, price range, room types, `session_id`

### 3. Load More Hotels (pagination)

```
integration.sh "travel/hotels/more?session_id=abc123"
```

### 4. Hotel Details

```
integration.sh "travel/hotels/details?session_id=abc123&hotel_id=xyz789"
```

Response: full description, room options, rates, cancellation policies, amenities

### 5. Book Hotel (generates payment link)

```
integration.sh "travel/hotels/book?session_id=abc123&hotel_id=xyz789&rate_id=rate123"
```

Response: booking quote + payment URL. **Always confirm with user before calling.**

### 6. Get Facilities (for filtering)

```
integration.sh "travel/hotels/facilities?language=en"
```

Response: facility IDs and names (WiFi, pool, parking, pet-friendly, etc.)

---

## Flights (FCTOLabs — real-time pricing)

### 1. Search Flights

```
integration.sh "travel/flights/search?origin=ICN&destination=NRT&depart_date=2026-04-01&return_date=2026-04-05"
```

Parameters:
- `origin`, `destination` — city name or IATA code (e.g., Seoul, ICN, Tokyo, NRT). City names often work better.
- `depart_date` — departure date (YYYY-MM-DD)
- `return_date` — optional return date for round-trip

Response: flights with airline, times, duration, price, booking URL

### 2. Calendar Search (cheapest dates)

```
integration.sh "travel/flights/calendar?origin=ICN&destination=NRT&date=2026-04"
```

Parameters:
- `origin`, `destination` — IATA airport codes (required)
- `date` — month (YYYY-MM) or specific date (YYYY-MM-DD) (required)

Response: price per day for the month — great for finding cheapest travel dates

### 3. Reference Data (airports, airlines)

```
integration.sh "travel/flights/reference?type=airport&query=Seoul"
integration.sh "travel/flights/reference?type=airline&query=Korean Air"
```

Parameters:
- `type` — `airports`, `cities`, `airlines`, or `countries` (required)
- `action` — `search` or `list` (required)
- `query` — search term

### 4. Discover Flights (popular routes, deals)

```
integration.sh "travel/flights/discover?type=popular_routes&origin=ICN"
```

Parameters:
- `type` — `popular_routes`, `alternative_directions`, or `special_offers` (required)
- `origin` — IATA airport code

Response: popular destinations from origin with typical prices

---

## Stays (Airbnb)

### 1. Search Stays

```
integration.sh "travel/stays/search?location=Seoul, South Korea&checkin=2026-04-01&checkout=2026-04-03&adults=2"
```

Parameters:
- `location` (required) — city or region name
- `checkin`, `checkout` — dates (YYYY-MM-DD)
- `adults`, `children`, `infants`, `pets` — guest counts
- `minPrice`, `maxPrice` — price range per night
- `cursor` — pagination cursor

Response: listings with title, price, rating, type, Airbnb link

### 2. Listing Details

```
integration.sh "travel/stays/details?id=12345678"
```

Response: full description, amenities, house rules, location, photos link

---

## Workflow Example

User: "4월 초에 도쿄 여행 가고 싶어. 항공편이랑 숙소 알아봐줘"

1. `integration.sh "travel/flights/search?origin=ICN&destination=NRT&depart_date=2026-04-01&return_date=2026-04-05"`
2. `integration.sh "travel/hotels/find-place?query=Tokyo&language=ko"`
3. `integration.sh "travel/hotels/search?latitude=35.6762&longitude=139.6503&check_in_date=2026-04-01&check_out_date=2026-04-05&adults=1"`
4. Present flight options + hotel options with prices
5. If user wants Airbnb: `integration.sh "travel/stays/search?location=Tokyo, Japan&checkin=2026-04-01&checkout=2026-04-05&adults=1"`
