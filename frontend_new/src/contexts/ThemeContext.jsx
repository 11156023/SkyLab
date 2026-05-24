import { createContext, useContext, useEffect, useState } from "react";

const ThemeContext = createContext(null);

const STORAGE_KEY = "SkyLab-theme";

function getSystemTheme() {
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function ThemeProvider({ children }) {
  const [mode, setMode] = useState(() => {
    if (typeof window === "undefined") return "system";
    return localStorage.getItem(STORAGE_KEY) || "system";
  });

  // 解析出實際套用的 theme（light / dark）
  const [resolvedTheme, setResolvedTheme] = useState(() => {
    if (typeof window === "undefined") return "light";
    const saved = localStorage.getItem(STORAGE_KEY) || "system";
    return saved === "system" ? getSystemTheme() : saved;
  });

  useEffect(() => {
    if (mode === "system") {
      const mq = window.matchMedia("(prefers-color-scheme: dark)");
      const handler = (e) => {
        const t = e.matches ? "dark" : "light";
        setResolvedTheme(t);
        document.body.classList.toggle("dark", e.matches);
      };
      const initial = mq.matches ? "dark" : "light";
      setResolvedTheme(initial);
      document.body.classList.toggle("dark", mq.matches);
      mq.addEventListener("change", handler);
      return () => mq.removeEventListener("change", handler);
    } else {
      setResolvedTheme(mode);
      document.body.classList.toggle("dark", mode === "dark");
    }
    localStorage.setItem(STORAGE_KEY, mode);
  }, [mode]);

  return (
    <ThemeContext.Provider value={{ theme: resolvedTheme, mode, setMode }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme() {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used within ThemeProvider");
  return ctx;
}
