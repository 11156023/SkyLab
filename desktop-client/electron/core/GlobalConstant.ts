class GlobalConstant {
  public static DEFAULT_LANGUAGE = "zh-TW";
  /**
   * 早期版本內建的後端網址。那時只有北商一間學校在用，還連著這些網址的安裝
   * 一律是北商的使用者，啟動時改連北商現在的網址；新安裝沒有預設，要自己填學校的網址。
   */
  public static LEGACY_DEFAULT_BACKEND_URLS = [
    "http://localhost:8000",
    "https://skylab.ntubimdbirc.tw",
    "https://skylab-tw.com"
  ];
  public static LEGACY_DEFAULT_BACKEND_REPLACEMENT =
    "https://ntub.skylab-tw.com";
  public static TUNNEL_STATUS_POLL_INTERVAL_MS = 1000;
}

export default GlobalConstant;
