import { describe, it, expect } from '@jest/globals'
import {
  getCurrentSession,
  isMarketOpen,
  getNextMarketOpen,
  canTrade,
} from './alpaca-market-hours.js'

// Helper: create a Date at a specific ET time by constructing a UTC Date
// that, when converted to ET, gives the desired local time.
// We use Intl to figure out the current ET offset (handles DST).
function makeETDate(
  year: number,
  month: number,
  day: number,
  hours: number,
  minutes: number,
): Date {
  // Build a date string in ET and resolve to UTC
  // We need the UTC equivalent of the given ET time
  const etDateStr = `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}T${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:00`
  // Create a formatter that tells us the UTC offset for ET at this date
  const tempDate = new Date(etDateStr + 'Z')
  const etFormatter = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York',
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
    hour12: false,
  })

  // Determine if DST is active by checking the offset
  // Create date at noon on the target day to avoid edge cases
  const noonUTC = new Date(Date.UTC(year, month - 1, day, 17, 0, 0)) // noon ET is ~17:00 UTC
  const etParts = etFormatter.formatToParts(noonUTC)
  const etHour = Number(etParts.find(p => p.type === 'hour')?.value ?? '0')
  // At 17:00 UTC: EDT (UTC-4) shows 13, EST (UTC-5) shows 12
  const isDST = etHour === 13
  const offsetHours = isDST ? 4 : 5

  // Now construct the correct UTC date
  return new Date(Date.UTC(year, month - 1, day, hours + offsetHours, minutes, 0))

  // We don't use tempDate for anything but ensuring correct resolution
  void tempDate
}

// ── getCurrentSession ────────────────────────────────────────────────────────

describe('getCurrentSession', () => {
  it('should return PRE_MARKET at 8:00 ET on weekday', () => {
    // 2026-03-16 is a Monday
    const date = makeETDate(2026, 3, 16, 8, 0)
    expect(getCurrentSession(date)).toBe('PRE_MARKET')
  })

  it('should return REGULAR at 10:00 ET on weekday', () => {
    const date = makeETDate(2026, 3, 16, 10, 0)
    expect(getCurrentSession(date)).toBe('REGULAR')
  })

  it('should return REGULAR at 9:30 ET exactly', () => {
    const date = makeETDate(2026, 3, 16, 9, 30)
    expect(getCurrentSession(date)).toBe('REGULAR')
  })

  it('should return POST_MARKET at 17:00 ET on weekday', () => {
    const date = makeETDate(2026, 3, 16, 17, 0)
    expect(getCurrentSession(date)).toBe('POST_MARKET')
  })

  it('should return CLOSED at 22:00 ET on weekday', () => {
    const date = makeETDate(2026, 3, 16, 22, 0)
    expect(getCurrentSession(date)).toBe('CLOSED')
  })

  it('should return CLOSED on Saturday', () => {
    // 2026-03-14 is a Saturday
    const date = makeETDate(2026, 3, 14, 10, 0)
    expect(getCurrentSession(date)).toBe('CLOSED')
  })

  it('should return CLOSED on Sunday', () => {
    // 2026-03-15 is a Sunday
    const date = makeETDate(2026, 3, 15, 10, 0)
    expect(getCurrentSession(date)).toBe('CLOSED')
  })

  it('should return PRE_MARKET at 4:00 ET exactly', () => {
    const date = makeETDate(2026, 3, 16, 4, 0)
    expect(getCurrentSession(date)).toBe('PRE_MARKET')
  })

  it('should return CLOSED at 3:59 ET', () => {
    const date = makeETDate(2026, 3, 16, 3, 59)
    expect(getCurrentSession(date)).toBe('CLOSED')
  })

  it('should return POST_MARKET at 16:00 ET exactly', () => {
    const date = makeETDate(2026, 3, 16, 16, 0)
    expect(getCurrentSession(date)).toBe('POST_MARKET')
  })

  it('should return CLOSED at 20:00 ET exactly', () => {
    const date = makeETDate(2026, 3, 16, 20, 0)
    expect(getCurrentSession(date)).toBe('CLOSED')
  })
})

// ── isMarketOpen ─────────────────────────────────────────────────────────────

describe('isMarketOpen', () => {
  it('should return true during regular hours', () => {
    const date = makeETDate(2026, 3, 16, 10, 30)
    expect(isMarketOpen(date)).toBe(true)
  })

  it('should return false during pre-market', () => {
    const date = makeETDate(2026, 3, 16, 7, 0)
    expect(isMarketOpen(date)).toBe(false)
  })

  it('should return false during post-market', () => {
    const date = makeETDate(2026, 3, 16, 18, 0)
    expect(isMarketOpen(date)).toBe(false)
  })

  it('should return false when closed', () => {
    const date = makeETDate(2026, 3, 14, 10, 0) // Saturday
    expect(isMarketOpen(date)).toBe(false)
  })
})

// ── getNextMarketOpen ────────────────────────────────────────────────────────

describe('getNextMarketOpen', () => {
  it('should return next 9:30 ET when currently closed on weekday evening', () => {
    // Monday 22:00 ET -> Tuesday 9:30 ET
    const date = makeETDate(2026, 3, 16, 22, 0)
    const next = getNextMarketOpen(date)

    // Convert result to ET and check
    const etString = next.toLocaleString('en-US', { timeZone: 'America/New_York' })
    const etDate = new Date(etString)
    expect(etDate.getHours()).toBe(9)
    expect(etDate.getMinutes()).toBe(30)

    // Should be Tuesday (day after Monday)
    expect(next.getTime()).toBeGreaterThan(date.getTime())
  })

  it('should return Monday 9:30 ET when currently Saturday', () => {
    // 2026-03-14 Saturday -> 2026-03-16 Monday 9:30 ET
    const date = makeETDate(2026, 3, 14, 10, 0)
    const next = getNextMarketOpen(date)

    const etString = next.toLocaleString('en-US', { timeZone: 'America/New_York' })
    const etDate = new Date(etString)
    expect(etDate.getHours()).toBe(9)
    expect(etDate.getMinutes()).toBe(30)

    // Should be at least 2 days later (Saturday -> Monday)
    const dayDiff = (next.getTime() - date.getTime()) / (24 * 60 * 60 * 1000)
    expect(dayDiff).toBeGreaterThanOrEqual(1.4) // At least ~1.5 days from Sat 10:00 to Mon 9:30
  })

  it('should return same day 9:30 ET when currently pre-market', () => {
    // Monday 7:00 ET -> same day 9:30 ET
    const date = makeETDate(2026, 3, 16, 7, 0)
    const next = getNextMarketOpen(date)

    const etString = next.toLocaleString('en-US', { timeZone: 'America/New_York' })
    const etDate = new Date(etString)
    expect(etDate.getHours()).toBe(9)
    expect(etDate.getMinutes()).toBe(30)

    // Should be same day, about 2.5 hours later
    const hourDiff = (next.getTime() - date.getTime()) / (60 * 60 * 1000)
    expect(hourDiff).toBeCloseTo(2.5, 0)
  })

  it('should return next day 9:30 ET when currently during regular hours', () => {
    // During regular hours, next open is the following trading day
    const date = makeETDate(2026, 3, 16, 12, 0) // Monday noon
    const next = getNextMarketOpen(date)

    // Should be Tuesday 9:30 ET
    expect(next.getTime()).toBeGreaterThan(date.getTime())
    const etString = next.toLocaleString('en-US', { timeZone: 'America/New_York' })
    const etDate = new Date(etString)
    expect(etDate.getHours()).toBe(9)
    expect(etDate.getMinutes()).toBe(30)
  })
})

// ── canTrade ─────────────────────────────────────────────────────────────────

describe('canTrade', () => {
  it('should return true for REGULAR session regardless of extendedHours', () => {
    expect(canTrade('REGULAR', true)).toBe(true)
    expect(canTrade('REGULAR', false)).toBe(true)
  })

  it('should return true for PRE_MARKET when extendedHours=true', () => {
    expect(canTrade('PRE_MARKET', true)).toBe(true)
  })

  it('should return false for PRE_MARKET when extendedHours=false', () => {
    expect(canTrade('PRE_MARKET', false)).toBe(false)
  })

  it('should return true for POST_MARKET when extendedHours=true', () => {
    expect(canTrade('POST_MARKET', true)).toBe(true)
  })

  it('should return false for POST_MARKET when extendedHours=false', () => {
    expect(canTrade('POST_MARKET', false)).toBe(false)
  })

  it('should return false for CLOSED always', () => {
    expect(canTrade('CLOSED', true)).toBe(false)
    expect(canTrade('CLOSED', false)).toBe(false)
  })
})
