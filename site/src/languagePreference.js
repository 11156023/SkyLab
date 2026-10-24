/**
 * 依瀏覽器語言決定要看哪個語言頁。只有預設語言的根網址（/，也是 x-default）會轉：
 * 預渲染時在它的 <head> 放一小段腳本，頁面畫出來之前就換到 /en/ 或 /ja/；
 * /en/、/ja/ 是特地點進來的，不轉，搜尋引擎（瀏覽器回報英文）也能爬到每個語言。
 * 在語言選單點過的語言記在 localStorage，之後以它為準。
 */
import { DEFAULT_LANGUAGE, LANGUAGES } from "./languages";

export const LANGUAGE_STORAGE_KEY = "skylab.landing.lang";

/** 記下使用者在語言選單點的語言；無痕模式等 localStorage 不可用時只是不記 */
export function rememberLanguageChoice(code) {
  try {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, code);
  } catch {
    // 不記也能用，只是下次回到根網址會再依瀏覽器語言判斷
  }
}

/**
 * 根網址 <head> 裡的轉址腳本。偏好裡先遇到中文就留下；先遇到日文、英文就去該語言頁；
 * 偏好裡一個支援的都沒有時去英文頁（比中文更可能看得懂）。
 */
export function languageRedirectScript() {
  const paths = Object.fromEntries(LANGUAGES.map((lang) => [lang.code, lang.path]));
  return `(function(){
if(/bot|crawl|spider|slurp/i.test(navigator.userAgent))return;
var paths=${JSON.stringify(paths)};
var chosen=null;try{chosen=localStorage.getItem(${JSON.stringify(LANGUAGE_STORAGE_KEY)})}catch(e){}
var lang=paths[chosen]?chosen:null;
if(!lang){var langs=navigator.languages&&navigator.languages.length?navigator.languages:[navigator.language];
for(var i=0;i<langs.length&&!lang;i++){var code=String(langs[i]||"").toLowerCase();
if(code.indexOf("zh")===0)lang="zh-TW";else if(code.indexOf("ja")===0)lang="ja";else if(code.indexOf("en")===0)lang="en"}
if(!lang&&langs.length&&langs[0])lang="en"}
if(lang&&lang!==${JSON.stringify(DEFAULT_LANGUAGE)})location.replace(paths[lang]+location.search+location.hash)})()`;
}
