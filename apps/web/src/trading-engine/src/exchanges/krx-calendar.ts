// === KRX Market Calendar ===
// Korean Exchange (한국거래소) trading sessions and holiday calendar

export type KrxSession = 'PRE_MARKET' | 'REGULAR' | 'AFTER_HOURS' | 'CLOSED'

export interface KrxHoliday {
  date: string       // 'MM-DD' for fixed, 'YYYY-MM-DD' for lunar-dependent
  name: string
  nameKo: string
}

/** Fixed-date holidays (solar calendar) */
const FIXED_HOLIDAYS: KrxHoliday[] = [
  { date: '01-01', name: 'New Year', nameKo: '신정' },
  { date: '03-01', name: 'Independence Movement Day', nameKo: '삼일절' },
  { date: '05-05', name: "Children's Day", nameKo: '어린이날' },
  { date: '06-06', name: 'Memorial Day', nameKo: '현충일' },
  { date: '08-15', name: 'Liberation Day', nameKo: '광복절' },
  { date: '10-03', name: 'National Foundation Day', nameKo: '개천절' },
  { date: '10-09', name: 'Hangul Day', nameKo: '한글날' },
  { date: '12-25', name: 'Christmas', nameKo: '성탄절' },
  { date: '12-31', name: 'Year End (KRX)', nameKo: '연말 휴장' },
]

/** Lunar-dependent holiday names by date */
const LUNAR_HOLIDAY_NAMES: Record<string, string> = {
  // 설날 (Lunar New Year) — 3-day span
  '2026-02-16': '설날',
  '2026-02-17': '설날',
  '2026-02-18': '설날',
  '2026-05-24': '석가탄신일',
  '2026-10-04': '추석',
  '2026-10-05': '추석',
  '2026-10-06': '추석',
  '2027-02-05': '설날',
  '2027-02-06': '설날',
  '2027-02-07': '설날',
  '2027-05-13': '석가탄신일',
  '2027-09-23': '추석',
  '2027-09-24': '추석',
  '2027-09-25': '추석',
}

/**
 * Lunar-dependent holidays by year (설날, 석가탄신일, 추석).
 * Must be updated annually or fetched from an API.
 */
const LUNAR_HOLIDAYS: Record<number, string[]> = {
  2026: [
    '2026-02-16', '2026-02-17', '2026-02-18', // 설날 (Lunar New Year)
    '2026-05-24',                               // 석가탄신일 (Buddha's Birthday)
    '2026-10-04', '2026-10-05', '2026-10-06',  // 추석 (Chuseok)
  ],
  2027: [
    '2027-02-05', '2027-02-06', '2027-02-07',
    '2027-05-13',
    '2027-09-23', '2027-09-24', '2027-09-25',
  ],
}

/** KST offset in minutes: UTC+9 */
const KST_OFFSET_MINUTES = 540

/** Convert any Date to KST hours/minutes/day-of-week */
function getKSTComponents(now: Date): { hours: number; minutes: number; dayOfWeek: number; year: number; month: number; day: number } {
  const utcMs = now.getTime()
  const kstMs = utcMs + KST_OFFSET_MINUTES * 60_000
  const kst = new Date(kstMs)

  return {
    hours: kst.getUTCHours(),
    minutes: kst.getUTCMinutes(),
    dayOfWeek: kst.getUTCDay(),
    year: kst.getUTCFullYear(),
    month: kst.getUTCMonth() + 1,
    day: kst.getUTCDate(),
  }
}

/** Format MM-DD from KST components */
function formatMMDD(month: number, day: number): string {
  return `${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`
}

/** Format YYYY-MM-DD from KST components */
function formatISODate(year: number, month: number, day: number): string {
  return `${year}-${formatMMDD(month, day)}`
}

/**
 * Get the current KRX trading session.
 * Sessions (KST):
 *   PRE_MARKET:  08:00 - 09:00
 *   REGULAR:     09:00 - 15:30
 *   AFTER_HOURS: 15:40 - 18:00
 *   CLOSED:      all other times, weekends, holidays
 */
export function getCurrentKrxSession(now?: Date): KrxSession {
  const date = now ?? new Date()
  const kst = getKSTComponents(date)

  // Weekend check
  if (kst.dayOfWeek === 0 || kst.dayOfWeek === 6) return 'CLOSED'

  // Holiday check
  if (isKrxHoliday(date)) return 'CLOSED'

  const totalMinutes = kst.hours * 60 + kst.minutes

  if (totalMinutes >= 480 && totalMinutes < 540) return 'PRE_MARKET'    // 08:00 - 09:00
  if (totalMinutes >= 540 && totalMinutes < 930) return 'REGULAR'       // 09:00 - 15:30
  if (totalMinutes >= 940 && totalMinutes < 1080) return 'AFTER_HOURS'  // 15:40 - 18:00
  return 'CLOSED'
}

/**
 * Check if KRX regular session is currently open.
 */
export function isKrxMarketOpen(now?: Date): boolean {
  return getCurrentKrxSession(now) === 'REGULAR'
}

/**
 * Check if a given date falls on a Korean public holiday
 * (fixed solar holidays or lunar-dependent holidays).
 */
export function isKrxHoliday(date: Date): boolean {
  const kst = getKSTComponents(date)
  const mmdd = formatMMDD(kst.month, kst.day)
  const isoDate = formatISODate(kst.year, kst.month, kst.day)

  // Check fixed holidays
  if (FIXED_HOLIDAYS.some(h => h.date === mmdd)) return true

  // Check lunar holidays
  const yearHolidays = LUNAR_HOLIDAYS[kst.year]
  if (yearHolidays?.includes(isoDate)) return true

  return false
}

/**
 * Get the Korean name of a holiday, or null if not a holiday.
 */
export function getKrxHolidayName(date: Date): string | null {
  const kst = getKSTComponents(date)
  const mmdd = formatMMDD(kst.month, kst.day)
  const isoDate = formatISODate(kst.year, kst.month, kst.day)

  // Check fixed holidays
  const fixed = FIXED_HOLIDAYS.find(h => h.date === mmdd)
  if (fixed) return fixed.nameKo

  // Check lunar holidays
  const lunarName = LUNAR_HOLIDAY_NAMES[isoDate]
  if (lunarName) return lunarName

  return null
}

/**
 * Get the next KRX market open time (09:00 KST on the next trading day).
 * Skips weekends and holidays.
 */
export function getNextKrxMarketOpen(now?: Date): Date {
  const date = now ?? new Date()
  const kst = getKSTComponents(date)
  const totalMinutes = kst.hours * 60 + kst.minutes

  // If before 09:00 KST on a trading day, return today 09:00 KST
  const isWeekend = kst.dayOfWeek === 0 || kst.dayOfWeek === 6
  if (!isWeekend && !isKrxHoliday(date) && totalMinutes < 540) {
    return makeKST0900(kst.year, kst.month, kst.day)
  }

  // Otherwise, advance to the next day and find a valid trading day
  let candidateYear = kst.year
  let candidateMonth = kst.month
  let candidateDay = kst.day

  for (let i = 0; i < 30; i++) {
    // Advance one day
    const next = new Date(Date.UTC(candidateYear, candidateMonth - 1, candidateDay + 1, 0, 0, 0))
    candidateYear = next.getUTCFullYear()
    candidateMonth = next.getUTCMonth() + 1
    candidateDay = next.getUTCDate()

    const dayOfWeek = next.getUTCDay()
    if (dayOfWeek === 0 || dayOfWeek === 6) continue

    // Check holiday: construct a date at noon KST for the candidate day
    const candidateDate = new Date(Date.UTC(candidateYear, candidateMonth - 1, candidateDay, 3, 0, 0)) // noon KST = 03:00 UTC
    if (isKrxHoliday(candidateDate)) continue

    return makeKST0900(candidateYear, candidateMonth, candidateDay)
  }

  // Fallback (should never happen with 30-day lookahead)
  return makeKST0900(kst.year, kst.month, kst.day + 1)
}

/** Create a Date representing 09:00 KST on a given date (09:00 KST = 00:00 UTC) */
function makeKST0900(year: number, month: number, day: number): Date {
  // 09:00 KST = 09:00 - 09:00 = 00:00 UTC
  return new Date(Date.UTC(year, month - 1, day, 0, 0, 0))
}

/**
 * Check if trading is allowed for the given session.
 * Only REGULAR is always tradeable. AFTER_HOURS requires explicit opt-in.
 */
export function canTradeKrx(session: KrxSession, afterHoursAllowed: boolean): boolean {
  if (session === 'REGULAR') return true
  if (session === 'AFTER_HOURS' && afterHoursAllowed) return true
  return false
}
