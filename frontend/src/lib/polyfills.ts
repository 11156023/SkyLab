// Polyfills run before any other module so subsequent imports (and any
// top-level code inside dependencies) see a complete environment.
//
// `crypto.randomUUID()` requires a Secure Context (HTTPS or localhost).
// When the app is served over HTTP from a LAN IP (e.g. http://192.168.x.x/)
// the method is undefined and the bundle throws on first call. We polyfill
// it using `crypto.getRandomValues`, which is available in all secure-and-
// non-secure contexts, to generate an RFC 4122 v4 UUID.

if (typeof crypto !== "undefined" && typeof crypto.randomUUID !== "function") {
  const polyfillRandomUUID =
    (): `${string}-${string}-${string}-${string}-${string}` => {
      const bytes = crypto.getRandomValues(new Uint8Array(16))
      bytes[6] = (bytes[6] & 0x0f) | 0x40 // version 4
      bytes[8] = (bytes[8] & 0x3f) | 0x80 // variant 10xx
      const hex = Array.from(bytes, (b) =>
        b.toString(16).padStart(2, "0"),
      ).join("")
      return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20, 32)}` as `${string}-${string}-${string}-${string}-${string}`
    }

  Object.defineProperty(crypto, "randomUUID", {
    value: polyfillRandomUUID,
    writable: true,
    configurable: true,
  })
}

export {}
