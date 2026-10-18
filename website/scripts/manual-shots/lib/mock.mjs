// Mock API registry for the manual screenshots.
//
// A route is [method, RegExp on the pathname, handler]. The handler gets
// { mt, url, query, body, headers, method, state } and returns a JSON value, or
// { __status, __body, __delay, __text, __contentType } for anything else.
// Routes added with use() win over routes added with register(), so a scene can
// override a shared default without touching the shared file.

// Every screenshot is taken at this instant (the browser clock is frozen to it),
// so dates in the UI and in mock data stay the same between runs.
export const NOW = Date.parse("2026-10-12T02:00:00Z"); // Monday 10:00 in Taipei
export const MIN = 60 * 1000;
export const H = 60 * MIN;
export const D = 24 * H;
export const iso = (ms) => new Date(ms).toISOString();
export const ago = (ms) => iso(NOW - ms);
export const later = (ms) => iso(NOW + ms);

export function createMock(lang) {
  const front = [];
  const back = [];
  const m = {
    lang,
    state: {},
    unmatched: new Map(),
    // Pick the text for the current UI language: m.L("Alice", "王小明").
    L: (en, zh) => (lang === "en" ? en : zh),
    register(list) {
      back.push(...list);
    },
    use(list) {
      front.unshift(...[...list].reverse());
    },
    match(method, pathname) {
      for (const [m2, re, handler] of [...front, ...back]) {
        if (m2 !== method && m2 !== "*") continue;
        const mt = pathname.match(re);
        if (mt) return { mt, handler };
      }
      return null;
    },
  };
  return m;
}
