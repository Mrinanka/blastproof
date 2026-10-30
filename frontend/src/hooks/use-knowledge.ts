import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { errorText } from "@/components/common/states";
import { api } from "@/lib/api/client";
import { unwrap } from "@/lib/api/errors";
import { uploadForm } from "@/lib/knowledge";

// The backend's maximum page; the page shows at most this many sources.
export const KNOWLEDGE_LIMIT = 100;

// Under "knowledge" so an upload, delete or rescan can invalidate ["knowledge"].
export const knowledgeKey = ["knowledge", "list", { limit: KNOWLEDGE_LIMIT }] as const;

export function useKnowledge(enabled: boolean) {
  return useQuery({
    queryKey: knowledgeKey,
    enabled,
    queryFn: () =>
      unwrap(api.GET("/v1/knowledge", { params: { query: { limit: KNOWLEDGE_LIMIT } } })),
  });
}

export function useUploadKnowledge() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ file, tables }: { file: File; tables: string[] }) =>
      unwrap(
        api.POST("/v1/knowledge", {
          // The generated type says `file: string` (OpenAPI "binary"); the serializer sends the
          // real File, and openapi-fetch then leaves Content-Type to the browser (the boundary).
          body: { file: file as unknown as string },
          bodySerializer: () => uploadForm(file, tables),
        }),
      ),
    onSettled: () => client.invalidateQueries({ queryKey: ["knowledge"] }),
  });
}

export function useDeleteKnowledge() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (source: string) =>
      unwrap(api.DELETE("/v1/knowledge/{source}", { params: { path: { source } } })),
    onSuccess: (_data, source) => toast.success(`Deleted ${source}`),
    onError: (error) => toast.error(errorText(error)),
    // A 404 means the list is stale, so it refreshes either way.
    onSettled: () => client.invalidateQueries({ queryKey: ["knowledge"] }),
  });
}
