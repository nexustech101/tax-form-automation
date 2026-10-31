/**
 * Sign-in with an API key, verified with GET /auth/me. The key is kept in sessionStorage, or in localStorage
 * when "remember on this device" is ticked. Any 401 signs out.
 */
import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { setApiKey, setUnauthorizedHandler } from "../api/client";
import { fetchMe } from "../api/keys";
import type { ApiKeyInfo } from "../api/types";

const KEY = "tda.key";

type Auth = {
  me: ApiKeyInfo | null;
  /** Why the last session ended (expired or revoked key), shown on the sign-in screen. */
  notice: string | null;
  signIn: (key: string, remember: boolean) => Promise<void>;
  signOut: (notice?: string) => void;
};

const AuthContext = createContext<Auth | null>(null);

export function useAuth(): Auth {
  const auth = useContext(AuthContext);
  if (!auth) throw new Error("useAuth outside AuthProvider");
  return auth;
}

function storedKey(): string | null {
  try {
    return sessionStorage.getItem(KEY) ?? localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

function store(key: string | null, remember = false) {
  try {
    sessionStorage.removeItem(KEY);
    localStorage.removeItem(KEY);
    if (key) (remember ? localStorage : sessionStorage).setItem(KEY, key);
  } catch {
    // storage blocked: signed in for this page only
  }
}

export function AuthProvider({ children, fallback }: { children: ReactNode; fallback: ReactNode }) {
  const queryClient = useQueryClient();
  const [me, setMe] = useState<ApiKeyInfo | null>(null);
  const [checking, setChecking] = useState(() => storedKey() !== null);
  const [notice, setNotice] = useState<string | null>(null);

  const signOut = useCallback(
    (reason?: string) => {
      store(null);
      setApiKey(null);
      setMe(null);
      setNotice(reason ?? null);
      queryClient.clear();
    },
    [queryClient],
  );

  const signIn = useCallback(async (key: string, remember: boolean) => {
    setApiKey(key);
    try {
      const info = await fetchMe();
      store(key, remember);
      setNotice(null);
      setMe(info);
    } catch (error) {
      setApiKey(null);
      setNotice(null); // the 401 handler ran for this attempt; the form shows the error instead
      throw error;
    }
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(() => signOut("Your key is no longer valid. Sign in again."));
    const key = storedKey();
    if (!key) return;
    setApiKey(key);
    fetchMe()
      .then(setMe)
      .catch(() => store(null))
      .finally(() => setChecking(false));
  }, [signOut]);

  if (checking) return fallback;
  return <AuthContext.Provider value={{ me, notice, signIn, signOut }}>{children}</AuthContext.Provider>;
}
