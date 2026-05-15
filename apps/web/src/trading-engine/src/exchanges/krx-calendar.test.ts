import {
  getCurrentKrxSession,
  isKrxMarketOpen,
  isKrxHoliday,
  getKrxHolidayName,
  getNextKrxMarketOpen,
  canTradeKrx,
} from './krx-calendar.js'
import type { KrxSession } from './krx-calendar.js'

/**
 * Helper: create a Date at a specific KST time.
 * KST = UTC+9, so we subtract 9 hours to get UTC.
 */
function kstDate(year: number, month: number, day: number, hour: number, minute: number): Date {
  return new Date(Date.UTC(year, month - 1, day, hour - 9, minute))
}

describe('KrxMarketCalendar', () => {
  describe('getCurrentKrxSession', () => {
    it('should return PRE_MARKET at 08:30 KST on weekday', () => {
      // 2026-03-16 is Monday
      const date = kstDate(2026, 3, 16, 8, 30)
      expect(getCurrentKrxSession(date)).toBe('PRE_MARKET')
    })

    it('should return REGULAR at 10:00 KST on weekday', () => {
      const date = kstDate(2026, 3, 16, 10, 0)
      expect(getCurrentKrxSession(date)).toBe('REGULAR')
    })

    it('should return REGULAR at 09:00 KST (session start)', () => {
      const date = kstDate(2026, 3, 16, 9, 0)
      expect(getCurrentKrxSession(date)).toBe('REGULAR')
    })

    it('should return AFTER_HOURS at 16:00 KST on weekday', () => {
      const date = kstDate(2026, 3, 16, 16, 0)
      expect(getCurrentKrxSession(date)).toBe('AFTER_HOURS')
    })

    it('should return CLOSED at 19:00 KST on weekday', () => {
      const date = kstDate(2026, 3, 16, 19, 0)
      expect(getCurrentKrxSession(date)).toBe('CLOSED')
    })

    it('should return CLOSED before 08:00 KST on weekday', () => {
      const date = kstDate(2026, 3, 16, 7, 30)
      expect(getCurrentKrxSession(date)).toBe('CLOSED')
    })

    it('should return CLOSED on Saturday', () => {
      // 2026-03-14 is Saturday
      const date = kstDate(2026, 3, 14, 10, 0)
      expect(getCurrentKrxSession(date)).toBe('CLOSED')
    })

    it('should return CLOSED on Sunday', () => {
      // 2026-03-15 is Sunday
      const date = kstDate(2026, 3, 15, 10, 0)
      expect(getCurrentKrxSession(date)).toBe('CLOSED')
    })

    it('should return CLOSED on Korean fixed holiday (삼일절 03-01)', () => {
      // 2027-03-01 is Monday
      const date = kstDate(2027, 3, 1, 10, 0)
      expect(getCurrentKrxSession(date)).toBe('CLOSED')
    })

    it('should return CLOSED on lunar holiday (설날)', () => {
      // 2026-02-17 is Lunar New Year (설날)
      const date = kstDate(2026, 2, 17, 10, 0)
      expect(getCurrentKrxSession(date)).toBe('CLOSED')
    })

    it('should return CLOSED between 15:30 and 15:40 KST (gap)', () => {
      const date = kstDate(2026, 3, 16, 15, 35)
      expect(getCurrentKrxSession(date)).toBe('CLOSED')
    })
  })

  describe('isKrxMarketOpen', () => {
    it('should return true during REGULAR session', () => {
      const date = kstDate(2026, 3, 16, 10, 0)
      expect(isKrxMarketOpen(date)).toBe(true)
    })

    it('should return false during CLOSED session', () => {
      const date = kstDate(2026, 3, 16, 19, 0)
      expect(isKrxMarketOpen(date)).toBe(false)
    })

    it('should return false during PRE_MARKET', () => {
      const date = kstDate(2026, 3, 16, 8, 30)
      expect(isKrxMarketOpen(date)).toBe(false)
    })
  })

  describe('isKrxHoliday', () => {
    it('should return true for fixed holidays (삼일절 03-01)', () => {
      const date = kstDate(2026, 3, 1, 12, 0)
      expect(isKrxHoliday(date)).toBe(true)
    })

    it('should return true for New Year (01-01)', () => {
      const date = kstDate(2026, 1, 1, 12, 0)
      expect(isKrxHoliday(date)).toBe(true)
    })

    it('should return true for Christmas (12-25)', () => {
      const date = kstDate(2026, 12, 25, 12, 0)
      expect(isKrxHoliday(date)).toBe(true)
    })

    it('should return true for KRX year-end closure (12-31)', () => {
      const date = kstDate(2026, 12, 31, 12, 0)
      expect(isKrxHoliday(date)).toBe(true)
    })

    it('should return true for lunar holidays (설날 2026-02-17)', () => {
      const date = kstDate(2026, 2, 17, 12, 0)
      expect(isKrxHoliday(date)).toBe(true)
    })

    it('should return true for 추석 (2026-10-05)', () => {
      const date = kstDate(2026, 10, 5, 12, 0)
      expect(isKrxHoliday(date)).toBe(true)
    })

    it('should return true for 석가탄신일 (2026-05-24)', () => {
      const date = kstDate(2026, 5, 24, 12, 0)
      expect(isKrxHoliday(date)).toBe(true)
    })

    it('should return false for regular weekdays', () => {
      // 2026-03-16 is a regular Monday
      const date = kstDate(2026, 3, 16, 12, 0)
      expect(isKrxHoliday(date)).toBe(false)
    })

    it('should return false for unknown years with no lunar data', () => {
      // 2030 is not in the lookup table
      const date = kstDate(2030, 2, 5, 12, 0)
      expect(isKrxHoliday(date)).toBe(false)
    })
  })

  describe('getKrxHolidayName', () => {
    it('should return Korean name for 삼일절', () => {
      const date = kstDate(2026, 3, 1, 12, 0)
      expect(getKrxHolidayName(date)).toBe('삼일절')
    })

    it('should return Korean name for 광복절', () => {
      const date = kstDate(2026, 8, 15, 12, 0)
      expect(getKrxHolidayName(date)).toBe('광복절')
    })

    it('should return Korean name for lunar holidays (설날)', () => {
      const date = kstDate(2026, 2, 17, 12, 0)
      expect(getKrxHolidayName(date)).toBe('설날')
    })

    it('should return null for non-holidays', () => {
      const date = kstDate(2026, 3, 16, 12, 0)
      expect(getKrxHolidayName(date)).toBeNull()
    })
  })

  describe('getNextKrxMarketOpen', () => {
    it('should return next 09:00 KST when currently closed on weekday evening', () => {
      // Monday 19:00 KST -> Tuesday 09:00 KST
      const now = kstDate(2026, 3, 16, 19, 0)
      const next = getNextKrxMarketOpen(now)
      expect(next.getUTCHours()).toBe(0) // 09:00 KST = 00:00 UTC
      expect(next.getUTCMinutes()).toBe(0)
      expect(next.getUTCDate()).toBe(17) // March 17
    })

    it('should skip weekends to Monday', () => {
      // Saturday 10:00 KST -> Monday 09:00 KST
      const now = kstDate(2026, 3, 14, 10, 0)
      const next = getNextKrxMarketOpen(now)
      // Monday March 16, 09:00 KST = March 16, 00:00 UTC
      expect(next.getUTCDate()).toBe(16)
      expect(next.getUTCHours()).toBe(0)
    })

    it('should skip holidays', () => {
      // 2026-02-16 (Mon) is 설날 holiday, 02-17 (Tue) is 설날, 02-18 (Wed) is 설날
      // Should skip to 02-19 (Thu) 09:00 KST
      const now = kstDate(2026, 2, 16, 10, 0)
      const next = getNextKrxMarketOpen(now)
      // Feb 19, 09:00 KST = Feb 19, 00:00 UTC
      expect(next.getUTCMonth()).toBe(1) // February (0-indexed)
      expect(next.getUTCDate()).toBe(19)
      expect(next.getUTCHours()).toBe(0)
    })

    it('should return same day 09:00 KST when currently pre-market', () => {
      // Monday 08:30 KST -> Monday 09:00 KST
      const now = kstDate(2026, 3, 16, 8, 30)
      const next = getNextKrxMarketOpen(now)
      expect(next.getUTCDate()).toBe(16)
      expect(next.getUTCHours()).toBe(0)
    })

    it('should advance to next day when market already closed for the day', () => {
      // Monday 15:31 KST -> Tuesday 09:00 KST
      const now = kstDate(2026, 3, 16, 15, 31)
      const next = getNextKrxMarketOpen(now)
      expect(next.getUTCDate()).toBe(17)
      expect(next.getUTCHours()).toBe(0)
    })

    it('should skip Friday evening to Monday', () => {
      // Friday 20:00 KST -> Monday 09:00 KST
      // 2026-03-20 is Friday
      const now = kstDate(2026, 3, 20, 20, 0)
      const next = getNextKrxMarketOpen(now)
      // Monday March 23, 09:00 KST = March 23, 00:00 UTC
      expect(next.getUTCDate()).toBe(23)
      expect(next.getUTCHours()).toBe(0)
    })
  })

  describe('canTradeKrx', () => {
    it('should return true for REGULAR session', () => {
      expect(canTradeKrx('REGULAR', false)).toBe(true)
    })

    it('should return true for AFTER_HOURS when afterHoursAllowed=true', () => {
      expect(canTradeKrx('AFTER_HOURS', true)).toBe(true)
    })

    it('should return false for AFTER_HOURS when afterHoursAllowed=false', () => {
      expect(canTradeKrx('AFTER_HOURS', false)).toBe(false)
    })

    it('should return false for CLOSED regardless of afterHoursAllowed', () => {
      expect(canTradeKrx('CLOSED', true)).toBe(false)
      expect(canTradeKrx('CLOSED', false)).toBe(false)
    })

    it('should return false for PRE_MARKET regardless of afterHoursAllowed', () => {
      expect(canTradeKrx('PRE_MARKET', true)).toBe(false)
      expect(canTradeKrx('PRE_MARKET', false)).toBe(false)
    })
  })
})
