interface GtagEventParams {
  [key: string]: string | number | boolean | null | undefined;
}

interface GtagConsentParams {
  analytics_storage?: "granted" | "denied";
  ad_storage?: "granted" | "denied";
  ad_user_data?: "granted" | "denied";
  ad_personalization?: "granted" | "denied";
}

interface Window {
  gtag?: {
    (command: "event" | "config" | "js", targetOrName: string | Date, params?: GtagEventParams): void;
    (command: "consent", action: "default" | "update", params: GtagConsentParams): void;
  };
  dataLayer?: unknown[];
}
