/**
 * US market hours awareness for the Alpaca adapter.
 * Handles session detection, DST-aware ET conversion, and next-open calculation.
 */

// ── Types ─────────────────────────────────────────────────────────────────────

export type MarketSession = 'PRE_MARKET' | 'REGULAR' | 'POST_MARKET' | 'CLOSED'

export interface MarketCalendarDay {
  date: string         // 'YYYY-MM-DD'
  open: string         // 'HH:MM' ET
  close: string        // 'HH:MM' ET
  sessionOpen: string  // pre-market open
  sessionClose: string // post-market close
}

// ── ET conversion (DST-aware) ─────────────────────────────────────────────────

interface ETComponents {
  year: number
  month: number
  day: number
  hours: number
  minutes: number
  dayOfWeek: number
}

function getETComponents(now: Date = new Date()): ETComponents {
  const formatter = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
    weekday: 'short',
  })

  const parts = formatter.formatToParts(now)

  const get = (type: Intl.DateTimeFormatPartTypes): string => {
    const part = parts.find(p => p.type === type)
    return part?.value ?? '0'
  }

  const weekdayStr = get('weekday')
  const dayMap: Record<string, number> = {
    'Sun': 0, 'Mon': 1, 'Tue': 2, 'Wed': 3, 'Thu': 4, 'Fri': 5, 'Sat': 6,
  }

  return {
    year: parseInt(get('year'), 10),
    month: parseInt(get('month'), 10),
    day: parseInt(get('day'), 10),
    hours: parseInt(get('hour'), 10),
    minutes: parseInt(get('minute'), 10),
    dayOfWeek: dayMap[weekdayStr] ?? 0,
  }
}

// ── Session boundaries (in minutes from midnight ET) ──────────────────────────

const PRE_MARKET_OPEN = 4 * 60          // 4:00 ET
const REGULAR_OPEN = 9 * 60 + 30        // 9:30 ET
const REGULAR_CLOSE = 16 * 60           // 16:00 ET
const POST_MARKET_CLOSE = 20 * 60       // 20:00 ET

// ── Public API ────────────────────────────────────────────────────────────────

/**
 * Determine the current US market session.
 */
export function getCurrentSession(now?: Date): MarketSession {
  const et = getETComponents(now)

  // Weekend
  if (et.dayOfWeek === 0 || et.dayOfWeek === 6) return 'CLOSED'

  const timeMinutes = et.hours * 60 + et.minutes

  if (timeMinutes >= PRE_MARKET_OPEN && timeMinutes < REGULAR_OPEN) return 'PRE_MARKET'
  if (timeMinutes >= REGULAR_OPEN && timeMinutes < REGULAR_CLOSE) return 'REGULAR'
  if (timeMinutes >= REGULAR_CLOSE && timeMinutes < POST_MARKET_CLOSE) return 'POST_MARKET'
  return 'CLOSED'
}

/**
 * Check if the market is currently in regular trading hours.
 */
export function isMarketOpen(now?: Date): boolean {
  return getCurrentSession(now) === 'REGULAR'
}

/**
 * Calculate the next market open (9:30 ET) from the given time.
 * Skips weekends. Does not account for holidays.
 */
export function getNextMarketOpen(now?: Date): Date {
  const currentDate = now ?? new Date()
  const et = getETComponents(currentDate)
  const timeMinutes = et.hours * 60 + et.minutes

  // Determine ET offset from UTC by comparing
  // We need to build a UTC date for the next 9:30 ET
  let targetDay = et.day
  let targetMonth = et.month
  let targetYear = et.year

  const isWeekday = et.dayOfWeek >= 1 && et.dayOfWeek <= 5
  const isBeforeOpen = timeMinutes < REGULAR_OPEN

  if (isWeekday && isBeforeOpen) {
    // Same day, 9:30 ET
  } else {
    // Next trading day
    let daysToAdd = 1
    let nextDow = et.dayOfWeek + 1

    if (!isWeekday) {
      // Weekend: Saturday (6) -> Monday = 2 days, Sunday (0) -> Monday = 1 day
      if (et.dayOfWeek === 6) {
        daysToAdd = 2
        nextDow = 1
      } else {
        daysToAdd = 1
        nextDow = 1
      }
    } else {
      // Weekday but after open (or during market hours)
      if (nextDow === 6) {
        // Friday -> Monday
        daysToAdd = 3
      } else if (nextDow === 0) {
        // Saturday -> Monday
        daysToAdd = 2
      }
    }

    // Add days using a Date object to handle month/year rollovers
    const tempDate = new Date(targetYear, targetMonth - 1, targetDay + daysToAdd)
    targetDay = tempDate.getDate()
    targetMonth = tempDate.getMonth() + 1
    targetYear = tempDate.getFullYear()

    void nextDow
  }

  // Build 9:30 ET on target day
  // Determine the UTC offset for that target day in ET
  const noonTarget = new Date(Date.UTC(targetYear, targetMonth - 1, targetDay, 17, 0, 0))
  const formatter = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York',
    hour: '2-digit',
    hour12: false,
  })
  const etHourAtNoon = parseInt(formatter.format(noonTarget), 10)
  // At 17:00 UTC: EDT (UTC-4) shows 13, EST (UTC-5) shows 12
  const isDST = etHourAtNoon === 13
  const offsetHours = isDST ? 4 : 5

  // 9:30 ET = 9:30 + offset in UTC
  return new Date(Date.UTC(targetYear, targetMonth - 1, targetDay, 9 + offsetHours, 30, 0))
}

/**
 * Check if trading is allowed for the given session and extended hours setting.
 */
export function canTrade(session: MarketSession, extendedHours: boolean): boolean {
  if (session === 'REGULAR') return true
  if (session === 'CLOSED') return false
  // PRE_MARKET and POST_MARKET require extendedHours
  return extendedHours
}
