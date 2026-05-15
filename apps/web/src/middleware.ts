import { NextResponse, type NextRequest } from "next/server";

/** EU + EEA + UK — regions requiring cookie consent under GDPR / ePrivacy */
const EU_COUNTRIES = new Set([
  "AT","BE","BG","HR","CY","CZ","DK","EE","FI","FR","DE","GR","HU","IE",
  "IT","LV","LT","LU","MT","NL","PL","PT","RO","SK","SI","ES","SE",
  "IS","LI","NO","GB",
]);

export function middleware(request: NextRequest) {
  const token = request.cookies.get("privy-token")?.value;

  const protectedPaths = ["/dashboard", "/onboarding"];
  const isProtected = protectedPaths.some((p) =>
    request.nextUrl.pathname.startsWith(p)
  );

  let response: NextResponse;

  if (!token && isProtected) {
    // Allow Stripe checkout returns — Privy client-side auth will kick in
    if (request.nextUrl.searchParams.has("session_id")) {
      response = NextResponse.next();
    } else {
      const returnUrl = request.nextUrl.pathname + request.nextUrl.search;
      const url = request.nextUrl.clone();
      url.pathname = "/login";
      url.search = "";
      url.searchParams.set("redirect", returnUrl);
      response = NextResponse.redirect(url);
    }
  } else {
    response = NextResponse.next();
  }

  // Geo cookie for consent mode — non-httpOnly so client JS can read it
  const country = request.headers.get("x-vercel-ip-country") ?? "";
  response.cookies.set("clawy_geo", EU_COUNTRIES.has(country) ? "eu" : "other", {
    httpOnly: false,
    secure: true,
    sameSite: "lax",
    maxAge: 86400,
  });

  return response;
}

export const config = {
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|html)$).*)",
  ],
};
