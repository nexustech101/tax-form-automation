/** Form review: read a form as checks, record or clear a decision, approve or reopen. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, unwrap } from "./client";
import { invalidateLibrary, keys } from "./library";
import type { FormDetail, ReviewRequest } from "./types";

export function useForm(id: string | null) {
  return useQuery({
    queryKey: keys.form(id ?? ""),
    enabled: id !== null,
    queryFn: () => unwrap(api.GET("/v1/library/forms/{form_id}", { params: { path: { form_id: id ?? "" } } })),
  });
}

export type ReviewChange = { field: string; review: ReviewRequest | null };

/** Sets (`review`) or clears (`review: null`) the decision on one field. */
export function useReviewField(form: FormDetail) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ field, review }: ReviewChange) => {
      const params = { path: { form_id: form.id, field } };
      return unwrap(
        review
          ? api.PUT("/v1/library/forms/{form_id}/reviews/{field}", { params, body: review })
          : api.DELETE("/v1/library/forms/{form_id}/reviews/{field}", { params }),
      );
    },
    onSuccess: (check) => {
      // Show the new decision at once; progress, status and counts come from the refetch.
      queryClient.setQueryData<FormDetail>(keys.form(form.id), (old) =>
        old && { ...old, checks: old.checks.map((c) => (c.field === check.field ? check : c)) },
      );
      return refresh(queryClient, form);
    },
  });
}

export function useFormStatus(form: FormDetail) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (action: "approve" | "reopen") => {
      const init = { params: { path: { form_id: form.id } } };
      return unwrap(
        action === "approve"
          ? api.POST("/v1/library/forms/{form_id}/approve", init)
          : api.POST("/v1/library/forms/{form_id}/reopen", init),
      );
    },
    onSuccess: (updated) => {
      queryClient.setQueryData(keys.form(form.id), updated);
      return refresh(queryClient, form, false);
    },
  });
}

function refresh(queryClient: ReturnType<typeof useQueryClient>, form: FormDetail, refetchForm = true) {
  return Promise.all([
    refetchForm && queryClient.invalidateQueries({ queryKey: keys.form(form.id) }),
    queryClient.invalidateQueries({ queryKey: keys.document(form.document_id) }),
    invalidateLibrary(queryClient),
  ]);
}
