// Follow the platform's configured release URL unless this deployment opts out.
const apiOrigin = (import.meta.env.VITE_API_URL || "").replace(/\/+$/, "");

export const DESKTOP_CLIENT_DOWNLOAD_URL =
  import.meta.env.VITE_DESKTOP_CLIENT_DOWNLOAD_URL ||
  `${apiOrigin}/api/v1/desktop-client/download`;
