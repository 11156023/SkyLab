/**
 * TotpEnrollment 示範用的假服務：不打後端，不會替任何帳號建立待確認的金鑰。
 * 金鑰是固定的示範值，QR 仍由 qrcode 套件在瀏覽器端畫出來。
 * 驗證碼只接受 DEMO_CODE，其餘一律回「驗證碼不正確」。
 */
export const DEMO_CODE = "123456";

const SETUP = {
  secret: "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP",
  otpauth_uri:
    "otpauth://totp/SkyLab:demo%40skylab.local?secret=JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP&issuer=SkyLab",
  account: "demo@skylab.local",
  issuer: "SkyLab",
};

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** 正常流程：取金鑰 600ms 後回來；只有 DEMO_CODE 會確認成功 */
export const fakeTotpService = {
  async setupTotp() {
    await delay(600);
    return { ...SETUP };
  },
  async confirmTotp(code) {
    await delay(500);
    if (code !== DEMO_CODE) {
      /* 不帶 message：讓元件退回語系檔的 TotpEnrollment.codeInvalid */
      throw {};
    }
    return { ok: true };
  },
};

/** 取金鑰失敗：示範 ErrorState＋取消鈕的失敗態 */
export const failingTotpService = {
  async setupTotp() {
    await delay(600);
    throw new Error("HTTP 500");
  },
  async confirmTotp() {
    throw {};
  },
};

/** 一直在「產生中」：示範 QR 佔位狀態 */
export const pendingTotpService = {
  setupTotp: () => new Promise(() => {}),
  confirmTotp: () => new Promise(() => {}),
};
