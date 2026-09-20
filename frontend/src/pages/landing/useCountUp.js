import { useEffect, useRef, useState } from "react";

/**
 * 元素進入視口時從 0 數到 target 的 count-up。
 * enabled=false（reduce-motion 或行動版關閉運鏡）時直接顯示終值。
 * 回傳 [目前顯示值, 要掛在元素上的 ref]。
 */
export default function useCountUp(target, { duration = 1400, enabled = true } = {}) {
  const [value, setValue] = useState(enabled ? 0 : target);
  const ref = useRef(null);
  const startedRef = useRef(false);

  useEffect(() => {
    if (!enabled) {
      setValue(target);
      return undefined;
    }
    const el = ref.current;
    if (!el || startedRef.current) return undefined;

    let raf = 0;
    const io = new IntersectionObserver(
      ([entry]) => {
        if (!entry.isIntersecting || startedRef.current) return;
        startedRef.current = true;
        io.disconnect();
        const t0 = performance.now();
        const tick = (now) => {
          const p = Math.min((now - t0) / duration, 1);
          const eased = 1 - (1 - p) ** 3;
          setValue(Math.round(target * eased));
          if (p < 1) raf = requestAnimationFrame(tick);
        };
        raf = requestAnimationFrame(tick);
      },
      { threshold: 0.4 },
    );
    io.observe(el);
    return () => {
      io.disconnect();
      cancelAnimationFrame(raf);
    };
  }, [target, duration, enabled]);

  return [value, ref];
}
