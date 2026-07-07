const { NextResponse } = require("next/server");

const SEE_OTHER = 303;

function firstHeaderValue(request, name) {
  return String(request.headers.get(name) || "")
    .split(",")
    .map((value) => value.trim())
    .find(Boolean);
}

function requestOrigin(request) {
  const fallback = new URL(request.url);
  const host = firstHeaderValue(request, "x-forwarded-host") || firstHeaderValue(request, "host") || fallback.host;
  const protocol = firstHeaderValue(request, "x-forwarded-proto") || fallback.protocol.replace(/:$/, "") || "http";
  return `${protocol}://${host}`;
}

function sameHostUrl(request, destination) {
  const origin = new URL(requestOrigin(request));
  const url = destination instanceof URL
    ? new URL(destination.toString())
    : new URL(String(destination), origin);

  url.protocol = origin.protocol;
  url.host = origin.host;
  return url;
}

function redirectSeeOther(request, destination) {
  return NextResponse.redirect(sameHostUrl(request, destination), SEE_OTHER);
}

function isSecureRequest(request) {
  const protocol = firstHeaderValue(request, "x-forwarded-proto") || new URL(request.url).protocol.replace(/:$/, "");
  return protocol === "https";
}

module.exports = {
  SEE_OTHER,
  isSecureRequest,
  redirectSeeOther,
  sameHostUrl,
};
