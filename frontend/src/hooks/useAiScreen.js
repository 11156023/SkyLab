import { useContext, useEffect, useRef } from "react";
import { LayoutContext } from "../layout/layoutContext";

// Callers explicitly select non-sensitive facts; never scrape the form or DOM.
export default function useAiScreen(id, state) {
  const { registerSurface } = useContext(LayoutContext);
  const snapshot = useRef(state);
  const version = useRef(0);
  snapshot.current = state;
  useEffect(() => { version.current += 1; });
  useEffect(() => {
    registerSurface(id, {
      getState: () => snapshot.current,
      getVersion: () => version.current,
    });
    return () => registerSurface(id, null);
  }, [id, registerSurface]);
}
