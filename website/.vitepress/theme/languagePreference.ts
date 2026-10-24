// Readers whose browser prefers Chinese land on the 繁體中文 version of an English page;
// a language picked in the nav menu wins from then on. The redirect is an inline script in
// the <head> of every English page that has a 繁體中文 version (config.mts, transformHead),
// so the English page never shows first; the menu choice is remembered here.
// Only English pages redirect: a 繁體中文 page was reached on purpose, and search engines
// (whose browsers report English) must be able to crawl both versions.

import type { Router } from 'vitepress'

export const LANGUAGE_STORAGE_KEY = 'skylab.docs.lang'

const ZH_PREFIX = '/zh-TW'

/** Inline script for an English page whose 繁體中文 version is at `target`. */
export function languageRedirectScript(target: string): string {
  return `(function(){
if(/bot|crawl|spider|slurp/i.test(navigator.userAgent))return;
var chosen=null;try{chosen=localStorage.getItem(${JSON.stringify(LANGUAGE_STORAGE_KEY)})}catch(e){}
var zh=chosen==="zh-TW";
if(!chosen){var langs=navigator.languages&&navigator.languages.length?navigator.languages:[navigator.language];
for(var i=0;i<langs.length;i++){var code=String(langs[i]).toLowerCase();if(code.indexOf("zh")===0){zh=true;break}if(code.indexOf("en")===0)break}}
if(zh)location.replace(${JSON.stringify(target)}+location.search+location.hash)})()`
}

/** /guides/x.html → /guides/x, /manual/index → /manual/ */
function normalize(pathname: string): string {
  return pathname.replace(/\.html$/, '').replace(/\/index$/, '/')
}

/** The same page in the other language: /guides/x ↔ /zh-TW/guides/x */
function otherLanguagePath(path: string): string {
  if (path === ZH_PREFIX || path.startsWith(`${ZH_PREFIX}/`)) return path.slice(ZH_PREFIX.length) || '/'
  return `${ZH_PREFIX}${path}`
}

/** Remembers a switch to the same page in the other language (what the language menu does). */
export function rememberLanguageChoice(router: Router) {
  const next = router.onBeforeRouteChange
  router.onBeforeRouteChange = (to) => {
    const from = normalize(location.pathname)
    const target = normalize(new URL(to, location.href).pathname)
    // Links to 繁體中文-only pages (e.g. from the English index) are not a language choice.
    if (target !== from && target === otherLanguagePath(from)) {
      try {
        localStorage.setItem(LANGUAGE_STORAGE_KEY, target.startsWith(`${ZH_PREFIX}/`) ? 'zh-TW' : 'en')
      } catch {
        // Storage is blocked (private mode): the choice lasts until the next full page load.
      }
    }
    return next?.(to)
  }
}
