const TRUNK_ZERO_COUNTRY_CODES = new Set(["82", "81", "44", "49", "33", "61", "886", "66", "84"]);

export function normalizeTelegramPhoneInput(countryCode: string, phone: string): string {
  const selectedCountryCode = countryCode.replace(/\D/g, "");
  const raw = phone.trim();
  const digits = raw.replace(/\D/g, "");

  if (!digits) return selectedCountryCode ? `+${selectedCountryCode}` : "";
  if (raw.startsWith("+")) return `+${digits}`;
  if (selectedCountryCode && digits.startsWith(selectedCountryCode)) return `+${digits}`;

  const localDigits = shouldDropTrunkZero(selectedCountryCode)
    ? digits.replace(/^0+/, "")
    : digits;

  return selectedCountryCode ? `+${selectedCountryCode}${localDigits}` : `+${localDigits}`;
}

function shouldDropTrunkZero(countryCode: string): boolean {
  return TRUNK_ZERO_COUNTRY_CODES.has(countryCode);
}
