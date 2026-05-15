"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { GlassCard } from "@/components/ui/glass-card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { useMessages } from "@/lib/i18n";
import { normalizeTelegramPhoneInput } from "@/lib/telegram/phone";
import { trackOnboardingTelegramValidate, trackOnboardingBotfatherClick, trackOnboardingNewbotCopy } from "@/lib/analytics";

interface StepTelegramProps {
  onConnect: (token: string, username: string) => void;
  initialToken?: string;
  initialUsername?: string;
}

// Country codes for the phone picker
const COUNTRY_CODES = [
  { code: "+82", flag: "\u{1F1F0}\u{1F1F7}", name: "KR" },
  { code: "+1", flag: "\u{1F1FA}\u{1F1F8}", name: "US" },
  { code: "+81", flag: "\u{1F1EF}\u{1F1F5}", name: "JP" },
  { code: "+86", flag: "\u{1F1E8}\u{1F1F3}", name: "CN" },
  { code: "+44", flag: "\u{1F1EC}\u{1F1E7}", name: "GB" },
  { code: "+49", flag: "\u{1F1E9}\u{1F1EA}", name: "DE" },
  { code: "+33", flag: "\u{1F1EB}\u{1F1F7}", name: "FR" },
  { code: "+65", flag: "\u{1F1F8}\u{1F1EC}", name: "SG" },
  { code: "+91", flag: "\u{1F1EE}\u{1F1F3}", name: "IN" },
  { code: "+61", flag: "\u{1F1E6}\u{1F1FA}", name: "AU" },
  { code: "+55", flag: "\u{1F1E7}\u{1F1F7}", name: "BR" },
  { code: "+7", flag: "\u{1F1F7}\u{1F1FA}", name: "RU" },
  { code: "+34", flag: "\u{1F1EA}\u{1F1F8}", name: "ES" },
  { code: "+39", flag: "\u{1F1EE}\u{1F1F9}", name: "IT" },
  { code: "+852", flag: "\u{1F1ED}\u{1F1F0}", name: "HK" },
  { code: "+886", flag: "\u{1F1F9}\u{1F1FC}", name: "TW" },
  { code: "+66", flag: "\u{1F1F9}\u{1F1ED}", name: "TH" },
  { code: "+84", flag: "\u{1F1FB}\u{1F1F3}", name: "VN" },
];

type EasyStep = "phone" | "code" | "2fa" | "name" | "creating" | "done";

export function StepTelegram({ onConnect, initialToken, initialUsername }: StepTelegramProps) {
  const [mode, setMode] = useState<"easy" | "advanced">("easy");
  const t = useMessages();

  return (
    <div>
      <h1 className="text-xl font-bold mb-1 text-gradient">{t.onboarding.telegramTitle}</h1>
      <p className="text-secondary text-sm mb-4">{t.onboarding.telegramSubtitle}</p>

      {/* Mode toggle */}
      <div className="flex gap-1 p-1 bg-gray-100 rounded-xl mb-5">
        <button
          type="button"
          onClick={() => setMode("easy")}
          className={`flex-1 py-2 px-3 rounded-lg text-sm font-medium transition-all ${
            mode === "easy"
              ? "bg-white text-foreground shadow-sm"
              : "text-secondary hover:text-foreground"
          }`}
        >
          {t.onboarding.telegramEasyTab}
        </button>
        <button
          type="button"
          onClick={() => setMode("advanced")}
          className={`flex-1 py-2 px-3 rounded-lg text-sm font-medium transition-all ${
            mode === "advanced"
              ? "bg-white text-foreground shadow-sm"
              : "text-secondary hover:text-foreground"
          }`}
        >
          {t.onboarding.telegramAdvancedTab}
        </button>
      </div>

      {mode === "easy" ? (
        <EasyMode onConnect={onConnect} />
      ) : (
        <AdvancedMode
          onConnect={onConnect}
          initialToken={initialToken}
          initialUsername={initialUsername}
        />
      )}
    </div>
  );
}

// ─── Easy Mode: Phone → Code → 2FA → Auto-create ───

function EasyMode({ onConnect }: { onConnect: (token: string, username: string) => void }) {
  const t = useMessages();
  const [step, setStep] = useState<EasyStep>("phone");
  const [countryCode, setCountryCode] = useState("+82");
  const [phone, setPhone] = useState("");
  const [sessionId, setSessionId] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [firstName, setFirstName] = useState("");
  const [botName, setBotName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [resendTimer, setResendTimer] = useState(0);
  const codeInputRef = useRef<HTMLInputElement>(null);
  const nameInputRef = useRef<HTMLInputElement>(null);

  // Resend countdown
  useEffect(() => {
    if (resendTimer <= 0) return;
    const timer = setTimeout(() => setResendTimer(r => r - 1), 1000);
    return () => clearTimeout(timer);
  }, [resendTimer]);

  // Auto-focus inputs
  useEffect(() => {
    if (step === "code") codeInputRef.current?.focus();
    if (step === "name") nameInputRef.current?.focus();
  }, [step]);

  const callAPI = useCallback(async (action: string, body: Record<string, string>) => {
    const res = await fetch("/api/onboarding/telegram-auto", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, ...body }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Request failed");
    return data;
  }, []);

  async function handleSendCode() {
    setLoading(true);
    setError(null);
    try {
      const fullPhone = normalizeTelegramPhoneInput(countryCode, phone);
      const data = await callAPI("send-code", { phone: fullPhone });
      setSessionId(data.sessionId);
      setStep("code");
      setResendTimer(60);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to send code");
    } finally {
      setLoading(false);
    }
  }

  function goToNameStep(name: string) {
    setFirstName(name);
    const defaultName = `${name}'s Open Magi Agent`;
    setBotName(defaultName);
    setStep("name");
  }

  async function handleVerifyCode() {
    setLoading(true);
    setError(null);
    try {
      const data = await callAPI("verify-code", { sessionId, code });
      if (data.needs2FA) {
        setStep("2fa");
      } else {
        goToNameStep(data.firstName || "My");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Verification failed");
    } finally {
      setLoading(false);
    }
  }

  async function handleVerify2FA() {
    setLoading(true);
    setError(null);
    try {
      const data = await callAPI("verify-2fa", { sessionId, password });
      goToNameStep(data.firstName || "My");
    } catch (err) {
      setError(err instanceof Error ? err.message : "2FA verification failed");
    } finally {
      setLoading(false);
    }
  }

  async function createBot() {
    setStep("creating");
    setError(null);
    try {
      const trimmed = botName.trim();
      const data = await callAPI("create-bot", trimmed ? { sessionId, botName: trimmed } : { sessionId });
      setStep("done");
      onConnect(data.token, data.username);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Bot creation failed");
      setStep("name"); // Back to name step, not phone
    }
  }

  async function handleResendCode() {
    const fullPhone = normalizeTelegramPhoneInput(countryCode, phone);
    setLoading(true);
    setError(null);
    try {
      const data = await callAPI("send-code", { phone: fullPhone });
      setSessionId(data.sessionId);
      setResendTimer(60);
      setCode("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to resend code");
    } finally {
      setLoading(false);
    }
  }

  if (step === "name") {
    return (
      <div>
        <GlassCard className="!p-5 !rounded-xl">
          <h2 className="text-base font-semibold text-center mb-1">{t.onboarding.telegramBotNameTitle}</h2>
          <p className="text-xs text-secondary text-center mb-5">{t.onboarding.telegramBotNameDesc}</p>

          <label className="block text-sm font-medium text-secondary mb-1.5">
            {t.onboarding.telegramBotNameLabel}
          </label>
          <input
            ref={nameInputRef}
            type="text"
            value={botName}
            onChange={(e) => setBotName(e.target.value.slice(0, 64))}
            placeholder={`${firstName}'s Open Magi Agent`}
            className="w-full bg-white border border-gray-300 rounded-xl px-4 py-3 text-foreground placeholder:text-gray-400 focus:outline-none focus:border-[#2AABEE]/50 focus:ring-1 focus:ring-[#2AABEE]/20 transition-colors"
            onKeyDown={(e) => { if (e.key === "Enter" && botName.trim()) createBot(); }}
          />
          <p className="text-[11px] text-secondary mt-1.5">{t.onboarding.telegramBotNameHint}</p>

          {error && <p className="text-red-500 text-sm mt-3">{error}</p>}

          <Button
            onClick={createBot}
            disabled={!botName.trim() || loading}
            className="w-full mt-4 !bg-[#2AABEE] hover:!bg-[#229ED9] !text-white"
            size="md"
          >
            {t.onboarding.telegramCreateBot}
          </Button>
        </GlassCard>
      </div>
    );
  }

  if (step === "creating") {
    return (
      <GlassCard className="!p-6 !rounded-xl text-center">
        <div className="animate-spin w-8 h-8 border-2 border-[#2AABEE] border-t-transparent rounded-full mx-auto mb-4" />
        <p className="text-sm font-medium text-foreground">{t.onboarding.telegramCreatingBot}</p>
        <p className="text-xs text-secondary mt-1">{t.onboarding.telegramCreatingDesc}</p>
      </GlassCard>
    );
  }

  return (
    <div>
      {/* Consent notice */}
      <p className="text-xs text-secondary mb-4 px-1">
        {t.onboarding.telegramAutoConsent}
      </p>

      {step === "phone" && (
        <GlassCard className="!p-5 !rounded-xl">
          {/* Telegram icon */}
          <div className="flex justify-center mb-4">
            <div className="w-16 h-16 rounded-full bg-[#2AABEE] flex items-center justify-center">
              <svg viewBox="0 0 24 24" className="w-8 h-8 text-white fill-current">
                <path d="M11.944 0A12 12 0 0 0 0 12a12 12 0 0 0 12 12 12 12 0 0 0 12-12A12 12 0 0 0 12 0a12 12 0 0 0-.056 0zm4.962 7.224c.1-.002.321.023.465.14a.506.506 0 0 1 .171.325c.016.093.036.306.02.472-.18 1.898-.962 6.502-1.36 8.627-.168.9-.499 1.201-.82 1.23-.696.065-1.225-.46-1.9-.902-1.056-.693-1.653-1.124-2.678-1.8-1.185-.78-.417-1.21.258-1.91.177-.184 3.247-2.977 3.307-3.23.007-.032.014-.15-.056-.212s-.174-.041-.249-.024c-.106.024-1.793 1.14-5.061 3.345-.48.33-.913.49-1.302.48-.428-.008-1.252-.241-1.865-.44-.752-.245-1.349-.374-1.297-.789.027-.216.325-.437.893-.663 3.498-1.524 5.83-2.529 6.998-3.014 3.332-1.386 4.025-1.627 4.476-1.635z" />
              </svg>
            </div>
          </div>

          <h2 className="text-base font-semibold text-center mb-1">{t.onboarding.telegramPhoneTitle}</h2>
          <p className="text-xs text-secondary text-center mb-5">{t.onboarding.telegramPhoneDesc}</p>

          <label className="block text-sm font-medium text-secondary mb-1.5">
            {t.onboarding.telegramPhoneLabel}
          </label>
          <div className="flex gap-2 mb-4">
            <select
              value={countryCode}
              onChange={(e) => setCountryCode(e.target.value)}
              className="w-24 bg-white border border-gray-300 rounded-xl px-2 py-3 text-sm focus:outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-colors cursor-pointer"
            >
              {COUNTRY_CODES.map((c) => (
                <option key={c.code} value={c.code}>
                  {c.flag} {c.code}
                </option>
              ))}
            </select>
            <input
              type="tel"
              inputMode="numeric"
              placeholder="10 1234 5678"
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
              className="flex-1 bg-white border border-gray-300 rounded-xl px-4 py-3 text-foreground placeholder:text-gray-400 focus:outline-none focus:border-[#2AABEE]/50 focus:ring-1 focus:ring-[#2AABEE]/20 transition-colors"
            />
          </div>

          {error && <p className="text-red-500 text-sm mb-3">{error}</p>}

          <Button
            onClick={handleSendCode}
            disabled={!phone.trim() || loading}
            className="w-full !bg-[#2AABEE] hover:!bg-[#229ED9] !text-white"
            size="md"
          >
            {loading ? t.onboarding.telegramSending : t.onboarding.telegramSendCode}
          </Button>
        </GlassCard>
      )}

      {step === "code" && (
        <GlassCard className="!p-5 !rounded-xl">
          <h2 className="text-base font-semibold text-center mb-1">{t.onboarding.telegramCodeTitle}</h2>
          <p className="text-xs text-secondary text-center mb-5">
            {t.onboarding.telegramCodeDesc} <strong>{countryCode}{phone}</strong>
          </p>

          <label className="block">
            <span className="block text-sm font-medium text-secondary mb-1.5">
              {t.onboarding.telegramCodeLabel}
            </span>
            <input
              ref={codeInputRef}
              type="text"
              inputMode="numeric"
              placeholder="12345"
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
              className="w-full bg-white border border-gray-300 rounded-xl px-4 py-3 text-foreground placeholder:text-gray-400 focus:outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-colors text-center text-lg tracking-[0.3em] font-mono"
              autoComplete="one-time-code"
            />
          </label>

          {error && <p className="text-red-500 text-sm mt-2">{error}</p>}

          <Button
            onClick={handleVerifyCode}
            disabled={code.length < 3 || loading}
            className="w-full mt-4 !bg-[#2AABEE] hover:!bg-[#229ED9] !text-white"
            size="md"
          >
            {loading ? t.onboarding.telegramVerifying : t.onboarding.telegramVerifyCode}
          </Button>

          <div className="flex justify-between items-center mt-3">
            <button
              type="button"
              onClick={() => { setStep("phone"); setError(null); }}
              className="text-xs text-secondary hover:text-foreground transition-colors"
            >
              {t.onboarding.telegramBack}
            </button>
            <button
              type="button"
              onClick={handleResendCode}
              disabled={resendTimer > 0 || loading}
              className={`text-xs transition-colors ${
                resendTimer > 0 ? "text-gray-400" : "text-[#2AABEE] hover:text-[#229ED9]"
              }`}
            >
              {resendTimer > 0
                ? `${t.onboarding.telegramResend} (${resendTimer}s)`
                : t.onboarding.telegramResend}
            </button>
          </div>
        </GlassCard>
      )}

      {step === "2fa" && (
        <GlassCard className="!p-5 !rounded-xl">
          <h2 className="text-base font-semibold text-center mb-1">{t.onboarding.telegram2faTitle}</h2>
          <p className="text-xs text-secondary text-center mb-5">{t.onboarding.telegram2faDesc}</p>

          <Input
            label={t.onboarding.telegram2faLabel}
            type="password"
            placeholder={t.onboarding.telegram2faPlaceholder}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
          />

          {error && <p className="text-red-500 text-sm mt-2">{error}</p>}

          <Button
            onClick={handleVerify2FA}
            disabled={!password || loading}
            className="w-full mt-4 !bg-[#2AABEE] hover:!bg-[#229ED9] !text-white"
            size="md"
          >
            {loading ? t.onboarding.telegramVerifying : t.onboarding.telegramVerify2fa}
          </Button>

          <button
            type="button"
            onClick={() => { setStep("phone"); setError(null); }}
            className="block mx-auto mt-3 text-xs text-secondary hover:text-foreground transition-colors"
          >
            {t.onboarding.telegramBack}
          </button>
        </GlassCard>
      )}
    </div>
  );
}

// ─── Advanced Mode: Manual BotFather flow (existing) ───

function AdvancedMode({
  onConnect,
  initialToken,
  initialUsername,
}: {
  onConnect: (token: string, username: string) => void;
  initialToken?: string;
  initialUsername?: string;
}) {
  const [token, setToken] = useState(initialToken ?? "");
  const [username, setUsername] = useState(initialUsername ?? "");
  const [validating, setValidating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [validated, setValidated] = useState(!!initialUsername);
  const [copied, setCopied] = useState(false);
  const t = useMessages();

  function handleCopyNewbot() {
    trackOnboardingNewbotCopy();
    navigator.clipboard.writeText("/newbot").then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }

  async function handleValidate() {
    setValidating(true);
    setError(null);

    try {
      const res = await fetch("/api/onboarding/validate-token", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token }),
      });
      const data = await res.json();

      if (!res.ok) {
        trackOnboardingTelegramValidate(false);
        setError(data.error);
        setValidated(false);
      } else {
        trackOnboardingTelegramValidate(true);
        setUsername(data.username);
        setValidated(true);
      }
    } catch {
      setError(t.onboarding.telegramNetworkError);
    } finally {
      setValidating(false);
    }
  }

  function handleConnect() {
    if (!validated) return;
    onConnect(token, username);
  }

  return (
    <div>
      <GlassCard className="mb-4 !p-4 !rounded-xl">
        <p className="font-semibold text-sm text-foreground mb-2">{t.onboarding.telegramHowTo}</p>
        <a
          href="https://t.me/BotFather?start"
          target="_blank"
          rel="noopener noreferrer"
          onClick={() => trackOnboardingBotfatherClick()}
          className="flex items-center gap-2.5 w-full mb-3 px-3.5 py-2.5 rounded-lg bg-[#2AABEE]/10 border border-[#2AABEE]/20 hover:bg-[#2AABEE]/20 transition-colors"
        >
          <span className="text-lg">{"\u{1F916}"}</span>
          <span className="flex-1">
            <span className="block text-sm font-semibold text-[#2AABEE]">{t.onboarding.telegramOpenBotFather}</span>
            <span className="block text-xs text-secondary">{t.onboarding.telegramOpenBotFatherDesc}</span>
          </span>
          <span className="text-secondary text-xs">{"\u2197"}</span>
        </a>
        <ol className="list-decimal list-inside space-y-2 text-xs text-secondary">
          <li className="flex items-center gap-2 flex-wrap">
            <span>{t.onboarding.telegramStep2Send}</span>
            <button
              type="button"
              onClick={handleCopyNewbot}
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-black/8 border border-black/10 hover:bg-black/12 transition-colors font-mono text-primary-light"
            >
              /newbot
              <span className="text-[10px] text-secondary">{copied ? t.onboarding.copied : t.onboarding.tapToCopy}</span>
            </button>
            <span>{t.onboarding.telegramStep2Follow}</span>
          </li>
          <li>{t.onboarding.telegramStep3}</li>
          <li>{t.onboarding.telegramStep4}</li>
        </ol>

        <div className="mt-3 pt-3 border-t border-black/5">
          <p className="text-[11px] text-secondary mb-2">BotFather will send you a message like this:</p>
          <div className="bg-black/30 rounded-lg px-3 py-2.5 font-mono text-xs leading-relaxed">
            <span className="text-secondary/60">Use this token to access the HTTP API:</span>
            {"\n"}
            <span className="text-primary-light font-bold">123456789:ABCDefGh-IjKLMnoPQRsTUVwxyz</span>
            {"\n"}
            <span className="text-secondary/60">Keep your token secure and store it safely</span>
          </div>
          <p className="text-[11px] text-secondary mt-2">
            {"\u{1F446}"} Tap the <span className="text-primary-light font-semibold">highlighted token</span> in BotFather to copy it
          </p>
        </div>
      </GlassCard>

      <div className="space-y-3">
        <Input
          label={t.onboarding.telegramTokenLabel}
          type="text"
          placeholder={t.onboarding.telegramTokenPlaceholder}
          value={token}
          onChange={(e) => {
            setToken(e.target.value);
            setValidated(false);
          }}
          className="font-mono text-sm"
        />

        {error && <p className="text-red-400 text-sm">{error}</p>}

        {validated && (
          <p className="text-emerald-600 text-sm">
            {t.onboarding.telegramVerified} <strong>@{username}</strong>
          </p>
        )}

        {!validated && (
          <Button
            variant="secondary"
            onClick={handleValidate}
            disabled={!token || validating}
            className="w-full"
            size="md"
          >
            {validating ? t.onboarding.telegramValidating : t.onboarding.telegramValidateBtn}
          </Button>
        )}
      </div>

      <Button
        onClick={handleConnect}
        disabled={!validated}
        size="md"
        className="w-full mt-5"
      >
        {t.onboarding.continue}
      </Button>
    </div>
  );
}
