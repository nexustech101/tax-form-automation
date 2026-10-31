/** The assistant is stateless: every call sends the whole conversation and the scope. */
import { useMutation } from "@tanstack/react-query";
import { api, unwrap } from "./client";
import type { ChatMessage, SearchFilters } from "./types";

type ChatRequest = { messages: ChatMessage[]; scope: SearchFilters; signal?: AbortSignal };

/** `signal` cancels the request (the mutation then fails with an `AbortError`). */
export function useChat() {
  return useMutation({
    mutationFn: ({ messages, scope, signal }: ChatRequest) =>
      unwrap(api.POST("/v1/assistant/chat", { body: { messages, scope }, signal })),
  });
}
