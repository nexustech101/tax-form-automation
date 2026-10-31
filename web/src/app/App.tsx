import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes } from "react-router";
import { ApiError } from "../api/client";
import { Spinner } from "../components/Spinner";
import { ToastProvider } from "../components/Toast";
import { pages } from "../features";
import { NotFound } from "./NotFound";
import { SignIn } from "./SignIn";
import { AssistantProvider } from "./assistant";
import { AuthProvider, useAuth } from "./auth";
import { Shell } from "./Shell";
import { ThemeProvider } from "./theme";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      refetchOnWindowFocus: false,
      // Client errors (404, 401, 409 ...) won't change on retry.
      retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2,
    },
  },
});

function Routed() {
  const { me } = useAuth();
  if (!me) return <SignIn />;
  return (
    <AssistantProvider>
      <Routes>
        <Route element={<Shell />}>
          {pages.map(({ path, component: Page }) => (
            <Route key={path} path={path} element={<Page />} />
          ))}
          <Route path="*" element={<NotFound />} />
        </Route>
      </Routes>
    </AssistantProvider>
  );
}

export function App() {
  return (
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <ToastProvider>
          <BrowserRouter>
            <AuthProvider fallback={<div className="boot"><Spinner size={20} label="Loading" /></div>}>
              <Routed />
            </AuthProvider>
          </BrowserRouter>
        </ToastProvider>
      </QueryClientProvider>
    </ThemeProvider>
  );
}
