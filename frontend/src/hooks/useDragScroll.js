import { useEffect } from "react";

/**
 * Mouse-drag + wheel + momentum horizontal scrolling.
 * Toggles `draggingClass` on the element while the user is actively dragging.
 *
 * Returns nothing — wire up via a ref passed to the scroll container.
 */
export function useDragScroll(ref, { draggingClass } = {}) {
  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    let dragging = false;
    let startX = 0;
    let startScroll = 0;
    let moved = 0;
    let latestX = 0;
    let target = 0;
    let current = el.scrollLeft;
    let velocity = 0;
    let lastSampleX = 0;
    let lastSampleT = 0;
    let rafId = null;

    const tick = () => {
      if (dragging) {
        current += (target - current) * 0.25;
        if (Math.abs(target - current) < 0.5) current = target;
        el.scrollLeft = current;
        rafId = requestAnimationFrame(tick);
        return;
      }
      if (Math.abs(velocity) < 0.02) {
        rafId = null;
        if (draggingClass) el.classList.remove(draggingClass);
        return;
      }
      current -= velocity * 16;
      velocity *= 0.95;
      const max = el.scrollWidth - el.clientWidth;
      if (current < 0) { current = 0; velocity = 0; }
      else if (current > max) { current = max; velocity = 0; }
      el.scrollLeft = current;
      rafId = requestAnimationFrame(tick);
    };

    const ensureLoop = () => {
      if (rafId == null) rafId = requestAnimationFrame(tick);
    };
    const cancelLoop = () => {
      if (rafId != null) {
        cancelAnimationFrame(rafId);
        rafId = null;
      }
    };

    const onWheel = (e) => {
      if (e.deltaY === 0) return;
      e.preventDefault();
      cancelLoop();
      velocity = 0;
      el.scrollLeft += e.deltaY;
      current = el.scrollLeft;
    };

    const onMouseDown = (e) => {
      if (e.button !== 0) return;
      cancelLoop();
      dragging = true;
      startX = e.pageX;
      latestX = e.pageX;
      lastSampleX = e.pageX;
      lastSampleT = performance.now();
      startScroll = el.scrollLeft;
      current = el.scrollLeft;
      target = el.scrollLeft;
      velocity = 0;
      moved = 0;
      if (draggingClass) el.classList.add(draggingClass);
      ensureLoop();
    };

    const onMouseMove = (e) => {
      if (!dragging) return;
      latestX = e.pageX;
      const dx = latestX - startX;
      moved = Math.abs(dx);
      target = startScroll - dx;
      const now = performance.now();
      const dt = now - lastSampleT;
      if (dt > 4) {
        const v = (latestX - lastSampleX) / dt;
        velocity = 0.7 * v + 0.3 * velocity;
        lastSampleX = latestX;
        lastSampleT = now;
      }
    };

    const onMouseUp = () => {
      if (!dragging) return;
      dragging = false;
      if (performance.now() - lastSampleT > 80) velocity = 0;
      ensureLoop();
    };

    /* Suppress click triggered by a drag */
    const onClickCapture = (e) => {
      if (moved > 5) {
        e.preventDefault();
        e.stopPropagation();
        moved = 0;
      }
    };

    el.addEventListener("wheel", onWheel, { passive: false });
    el.addEventListener("mousedown", onMouseDown);
    el.addEventListener("click", onClickCapture, true);
    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);

    return () => {
      cancelLoop();
      el.removeEventListener("wheel", onWheel);
      el.removeEventListener("mousedown", onMouseDown);
      el.removeEventListener("click", onClickCapture, true);
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
    };
  }, [ref, draggingClass]);
}
