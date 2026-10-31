import { LogOut, Monitor, Moon, Search, Sparkles, Sun } from "lucide-react";
import { useState, type FormEvent, type ReactNode } from "react";
import { matchPath, NavLink, Outlet, useLocation, useNavigate } from "react-router";
import { Button } from "../components/Button";
import { Segmented } from "../components/Segmented";
import { AssistantPanel } from "../features/assistant/AssistantPanel";
import { pages } from "../features";
import { useAssistant } from "./assistant";
import { useAuth } from "./auth";
import { useTheme, type ThemeChoice } from "./theme";
import styles from "./Shell.module.css";

const THEMES: { value: ThemeChoice; label: ReactNode; title: string }[] = [
  { value: "light", label: <><Sun aria-hidden /><span className="sr-only">Light</span></>, title: "Light theme" },
  { value: "dark", label: <><Moon aria-hidden /><span className="sr-only">Dark</span></>, title: "Dark theme" },
  { value: "system", label: <><Monitor aria-hidden /><span className="sr-only">System</span></>, title: "Follow the system theme" },
];

export function Shell() {
  const { me, signOut } = useAuth();
  const { choice, setChoice } = useTheme();
  const assistant = useAssistant();
  const { pathname } = useLocation();
  const page = pages.find((p) => matchPath(p.path, pathname));
  const initials = (me?.name ?? "?").slice(0, 2).toUpperCase();

  return (
    <div className={styles.app}>
      <aside className={styles.sidebar}>
        <div className={styles.brand}>
          <span className={styles.logo} aria-hidden>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M6 3h8l4 4v13a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z" />
              <path d="m9 13 2 2 4-4" />
            </svg>
          </span>
          <span className={styles.brandName}>Tax Documents</span>
        </div>
        <nav className={styles.nav} aria-label="Main">
          {pages
            .filter((p) => p.nav)
            .map(({ path, label, icon: Icon }) => (
              <NavLink key={path} to={path} end={path === "/"} className={styles.navLink} title={label}>
                {Icon && <Icon aria-hidden />}
                <span className={styles.navLabel}>{label}</span>
              </NavLink>
            ))}
        </nav>
        <div className={styles.account}>
          <span className={styles.avatar} aria-hidden>{initials}</span>
          <div className={styles.accountText}>
            <span className="truncate">{me?.name}</span>
            <span className={styles.accountRole}>{me?.is_admin ? "Admin key" : "Client key"}</span>
          </div>
          <Button variant="ghost" size="sm" icon={<LogOut />} aria-label="Sign out" title="Sign out" onClick={() => signOut()} />
        </div>
      </aside>
      <div className={styles.main}>
        <header className={styles.topbar}>
          <GlobalSearch />
          <div className={styles.topActions}>
            <Segmented label="Theme" value={choice} onChange={setChoice} options={THEMES} />
            {pathname !== "/assistant" && <Button
              icon={<Sparkles />}
              aria-pressed={assistant.panelOpen}
              onClick={assistant.panelOpen ? assistant.closePanel : assistant.openPanel}
            >
              Assistant
            </Button>}
          </div>
        </header>
        <main className={page?.fullBleed ? styles.full : styles.content}>
          <Outlet />
        </main>
      </div>
      {assistant.panelOpen && pathname !== "/assistant" && <AssistantPanel />}
    </div>
  );
}

function GlobalSearch() {
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    navigate(q.trim() ? `/documents?q=${encodeURIComponent(q.trim())}` : "/documents");
    setQ("");
  };
  return (
    <form className={styles.search} onSubmit={submit} role="search">
      <Search aria-hidden />
      <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Find documents by title or client" aria-label="Find documents" />
    </form>
  );
}
