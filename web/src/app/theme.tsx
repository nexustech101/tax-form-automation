/**
 * Theme: light (default), dark, or system. The choice is kept in localStorage ("tda.theme") and applied as
 * `data-theme` on <html>; index.html applies it before the first paint.
 */
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

export type ThemeChoice = "light" | "dark" | "system";

const KEY = "tda.theme";
const media = () => window.matchMedia("(prefers-color-scheme: dark)");

function stored(): ThemeChoice {
  try {
    const value = localStorage.getItem(KEY);
    return value === "dark" || value === "system" ? value : "light";
  } catch {
    return "light";
  }
}

const ThemeContext = createContext<{ choice: ThemeChoice; setChoice: (choice: ThemeChoice) => void }>({
  choice: "light",
  setChoice: () => {},
});

export const useTheme = () => useContext(ThemeContext);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [choice, setChoice] = useState(stored);
  useEffect(() => {
    try {
      localStorage.setItem(KEY, choice);
    } catch {
      // storage blocked: the choice lasts for this page only
    }
    const apply = () => {
      const dark = choice === "dark" || (choice === "system" && media().matches);
      document.documentElement.dataset.theme = dark ? "dark" : "light";
    };
    apply();
    if (choice !== "system") return;
    const query = media();
    query.addEventListener("change", apply);
    return () => query.removeEventListener("change", apply);
  }, [choice]);
  return <ThemeContext.Provider value={{ choice, setChoice }}>{children}</ThemeContext.Provider>;
}
