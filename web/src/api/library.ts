/** Documents, stats, page images and form types: one thin hook per resource. */
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { api, ok, unwrap } from "./client";
import type { DocumentFilters } from "./types";

/** Pages are always rendered at this width (px), so the viewer and every crop share one cached image. */
export const PAGE_WIDTH = 1600;

export const keys = {
  documents: ["documents"] as const,
  document: (id: string) => ["documents", id] as const,
  stats: ["stats"] as const,
  types: ["document-types"] as const,
  form: (id: string) => ["forms", id] as const,
  page: (id: string, page: number) => ["pages", id, page, PAGE_WIDTH] as const,
};

export type ListParams = DocumentFilters & { limit?: number; offset?: number; order?: "newest" | "oldest" };

export function useDocuments(params: ListParams) {
  return useQuery({
    queryKey: [...keys.documents, "list", params],
    queryFn: () => unwrap(api.GET("/v1/library/documents", { params: { query: params } })),
    placeholderData: keepPreviousData,
  });
}

export function useDocument(id: string) {
  return useQuery({
    queryKey: keys.document(id),
    queryFn: () =>
      unwrap(api.GET("/v1/library/documents/{document_id}", { params: { path: { document_id: id } } })),
  });
}

export function useStats(filters: DocumentFilters = {}) {
  return useQuery({
    queryKey: [...keys.stats, filters],
    queryFn: () => unwrap(api.GET("/v1/library/stats", { params: { query: filters } })),
  });
}

/** Supported forms; `name` is the short display name (W-2, 1099-NEC). Fetched once per session. */
export function useDocumentTypes() {
  return useQuery({
    queryKey: keys.types,
    queryFn: () => unwrap(api.GET("/v1/document-types")),
    staleTime: Infinity,
    gcTime: Infinity,
  });
}

/** Form id -> short name; unknown ids (e.g. `text`, `unknown`) map to themselves. */
export function useTypeName(): (id: string) => string {
  const { data } = useDocumentTypes();
  return useMemo(() => {
    const names = new Map((data ?? []).map((t) => [t.id, t.name]));
    names.set("text", "Text");
    names.set("unknown", "Unidentified");
    return (id: string) => names.get(id) ?? id;
  }, [data]);
}

export type UploadFields = { client?: string; tax_year?: number };

export function useUploadDocument() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ file, client, tax_year }: UploadFields & { file: File }) =>
      unwrap(
        api.POST("/v1/library/documents", {
          // `file` is binary in the schema (typed as string); the serializer sends the File itself.
          body: { file: file as unknown as string, client, tax_year, extract: true },
          bodySerializer(body) {
            const form = new FormData();
            form.set("file", file, file.name);
            if (body.client) form.set("client", body.client);
            if (body.tax_year) form.set("tax_year", String(body.tax_year));
            return form;
          },
        }),
      ),
    onSuccess: () => invalidateLibrary(queryClient),
  });
}

export function useDeleteDocument() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      ok(api.DELETE("/v1/library/documents/{document_id}", { params: { path: { document_id: id } } })),
    onSuccess: (_, id) => {
      queryClient.removeQueries({ queryKey: keys.document(id) });
      return invalidateLibrary(queryClient);
    },
  });
}

export function invalidateLibrary(queryClient: ReturnType<typeof useQueryClient>) {
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: keys.documents }),
    queryClient.invalidateQueries({ queryKey: keys.stats }),
  ]);
}

/** A rendered page as an object URL (null while loading). The PNG is fetched with the API key, then cached. */
export function usePageImage(documentId: string, page: number | null) {
  const query = useQuery({
    queryKey: keys.page(documentId, page ?? 0),
    enabled: page !== null,
    staleTime: Infinity,
    queryFn: () =>
      ok(
        api.GET("/v1/library/documents/{document_id}/pages/{page}", {
          params: { path: { document_id: documentId, page: page ?? 1 }, query: { width: PAGE_WIDTH } },
          parseAs: "blob",
        }),
      ) as Promise<Blob>,
  });
  return { url: useObjectUrl(query.data), isError: query.isError };
}

function useObjectUrl(blob: Blob | undefined): string | null {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!blob) {
      setUrl(null);
      return;
    }
    const next = URL.createObjectURL(blob);
    setUrl(next);
    return () => URL.revokeObjectURL(next);
  }, [blob]);
  return url;
}

/** Downloads the original file (the route needs the API key header, so a plain link cannot be used). */
export async function downloadOriginal(id: string, filename: string) {
  const blob = (await ok(
    api.GET("/v1/library/documents/{document_id}/file", {
      params: { path: { document_id: id } },
      parseAs: "blob",
    }),
  )) as Blob;
  const url = URL.createObjectURL(blob);
  const link = Object.assign(document.createElement("a"), { href: url, download: filename });
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
