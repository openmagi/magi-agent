import type { ChatResponseLanguage } from "./types";

function requestedOutputLanguage(text: string): ChatResponseLanguage | null {
  const lower = text.toLowerCase();
  if (/(?:in|into)\s+korean|korean\s+(?:answer|reply|response|version|translation|draft)|한국어로/u.test(lower)) {
    return "ko";
  }
  if (/(?:in|into)\s+english|english\s+(?:answer|reply|response|version|translation|draft)|영어로|영문(?:으로)?/u.test(lower)) {
    return "en";
  }
  if (/(?:in|into)\s+japanese|japanese\s+(?:answer|reply|response|version|translation|draft)|일본어로/u.test(lower)) {
    return "ja";
  }
  if (/(?:in|into)\s+chinese|chinese\s+(?:answer|reply|response|version|translation|draft)|중국어로/u.test(lower)) {
    return "zh";
  }
  if (/(?:in|into)\s+spanish|spanish\s+(?:answer|reply|response|version|translation|draft)|스페인어로/u.test(lower)) {
    return "es";
  }
  return null;
}

export function detectMessageResponseLanguage(text: string): ChatResponseLanguage {
  const requested = requestedOutputLanguage(text);
  if (requested) return requested;

  let hangul = 0;
  let kana = 0;
  let cjk = 0;
  let latin = 0;
  let spanishSignal = 0;

  for (const char of text) {
    if (/\p{Script=Hangul}/u.test(char)) hangul += 1;
    else if (/\p{Script=Hiragana}|\p{Script=Katakana}/u.test(char)) kana += 1;
    else if (/\p{Script=Han}/u.test(char)) cjk += 1;
    else if (/[A-Za-zÀ-ÖØ-öø-ÿ]/u.test(char)) {
      latin += 1;
      if (/[áéíóúüñ¿¡]/iu.test(char)) spanishSignal += 1;
    }
  }

  const spanishWords = ` ${text.toLowerCase()} `.match(
    /\b(?:el|la|los|las|un|una|que|para|por|con|como|gracias|hola|usted|ustedes|español)\b/g,
  );
  spanishSignal += spanishWords?.length ?? 0;

  if (kana >= 2) return "ja";
  if (hangul >= 2 && hangul >= latin * 0.35) return "ko";
  if (cjk >= 2 && cjk >= latin * 0.5) return "zh";
  if (latin >= 3) return spanishSignal >= 2 ? "es" : "en";
  return "en";
}
