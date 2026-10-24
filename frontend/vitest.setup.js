// 介面語言沒選過時依瀏覽器偏好決定（src/i18n/index.js）。happy-dom 的瀏覽器預設是 en-US，
// 測試一律模擬偏好繁體中文的瀏覽器，以中文文案斷言的測試才不會跟著環境變。
if (typeof window !== "undefined") {
  Object.defineProperty(window.navigator, "languages", { value: ["zh-TW"], configurable: true });
  Object.defineProperty(window.navigator, "language", { value: "zh-TW", configurable: true });
}
