/** The signed-in key, and API-key management (admin keys only). */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ok, unwrap } from "./client";

const apiKeys = ["api-keys"] as const;

export function fetchMe() {
  return unwrap(api.GET("/auth/me"));
}

export function useApiKeys(enabled: boolean) {
  return useQuery({ queryKey: apiKeys, enabled, queryFn: () => unwrap(api.GET("/api-keys")) });
}

export function useCreateApiKey() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { name: string; is_admin: boolean; expires_in_days: number | null }) =>
      unwrap(api.POST("/api-keys", { body })),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: apiKeys }),
  });
}

export function useRevokeApiKey() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => ok(api.DELETE("/api-keys/{key_id}", { params: { path: { key_id: id } } })),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: apiKeys }),
  });
}
