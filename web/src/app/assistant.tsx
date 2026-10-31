/**
 * The assistant conversation, shared by the slide-over panel and the full page. Kept in memory only: the API
 * is stateless, so every question sends the whole conversation and the scope.
 */
import { createContext, useCallback, useContext, useMemo, useRef, useState, type Dispatch, type ReactNode, type SetStateAction } from "react";
import { useChat } from "../api/assistant";
import type { ChatResponse, SearchFilters } from "../api/types";

export type Turn = { role: "user"; content: string } | { role: "assistant"; content: string; reply: ChatResponse };

export type ScopedDocument = { id: string; title: string };

/** Client and tax year, or documents: attaching a document clears client and year (all filters must match). */
export type Scope = { client?: string; tax_year?: number; documents: ScopedDocument[] };

type Assistant = {
  turns: Turn[];
  scope: Scope;
  setScope: Dispatch<SetStateAction<Scope>>;
  pending: boolean;
  /** The last request failed; `stopped` when the user cancelled it. */
  error: Error | null;
  stopped: boolean;
  /** Asks in the current scope, or first switches to `scope`. */
  ask: (question: string, scope?: Scope) => void;
  retry: () => void;
  stop: () => void;
  reset: () => void;
  panelOpen: boolean;
  openPanel: () => void;
  closePanel: () => void;
  /** The document the user is looking at (set by the review page): opening the panel scopes to it. */
  setCurrentDocument: (document: ScopedDocument | null) => void;
};

const AssistantContext = createContext<Assistant | null>(null);

export function useAssistant(): Assistant {
  const assistant = useContext(AssistantContext);
  if (!assistant) throw new Error("useAssistant outside AssistantProvider");
  return assistant;
}

export function toFilters(scope: Scope): SearchFilters {
  return {
    client: scope.client ?? null,
    tax_year: scope.tax_year ?? null,
    document_ids: scope.documents.length ? scope.documents.map((d) => d.id) : null,
  };
}

/** The scope with `document` added (client and year cleared). */
export function withDocument(scope: Scope, document: ScopedDocument): Scope {
  if (scope.documents.some((d) => d.id === document.id)) return scope;
  return { documents: [...scope.documents, document] };
}

export function AssistantProvider({ children }: { children: ReactNode }) {
  const chat = useChat();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [scope, setScope] = useState<Scope>({ documents: [] });
  const [panelOpen, setPanelOpen] = useState(false);
  const [currentDocument, setCurrentDocument] = useState<ScopedDocument | null>(null);
  const controller = useRef<AbortController | null>(null);
  // Bumped by reset: a reply to a cleared conversation is dropped.
  const conversation = useRef(0);

  const send = useCallback(
    (history: Turn[], filters: Scope) => {
      controller.current?.abort();
      const abort = (controller.current = new AbortController());
      const id = conversation.current;
      const messages = history.map(({ role, content }) => ({ role, content }));
      chat.mutate(
        { messages, scope: toFilters(filters), signal: abort.signal },
        {
          onSuccess: (reply) => {
            if (id === conversation.current) setTurns([...history, { role: "assistant", content: reply.message.content, reply }]);
          },
        },
      );
    },
    [chat],
  );

  const ask = useCallback(
    (question: string, next?: Scope) => {
      if (next) setScope(next);
      const history: Turn[] = [...turns, { role: "user", content: question }];
      setTurns(history);
      send(history, next ?? scope);
    },
    [turns, scope, send],
  );

  const value = useMemo<Assistant>(
    () => ({
      turns,
      scope,
      setScope,
      pending: chat.isPending,
      error: chat.error,
      stopped: chat.error?.name === "AbortError",
      ask,
      retry: () => send(turns, scope),
      stop: () => controller.current?.abort(),
      reset: () => {
        conversation.current++;
        controller.current?.abort();
        chat.reset();
        setTurns([]);
      },
      panelOpen,
      openPanel: () => {
        if (currentDocument) setScope({ documents: [currentDocument] });
        setPanelOpen(true);
      },
      closePanel: () => setPanelOpen(false),
      setCurrentDocument,
    }),
    [turns, scope, chat, ask, send, panelOpen, currentDocument],
  );
  return <AssistantContext.Provider value={value}>{children}</AssistantContext.Provider>;
}
