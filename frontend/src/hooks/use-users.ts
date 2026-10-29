import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api/client";
import { unwrap } from "@/lib/api/errors";
import type { UserValues } from "@/lib/users";

// The backend's maximum page; the page shows at most this many users.
export const USERS_LIMIT = 100;

export const usersKey = ["users", "list", { limit: USERS_LIMIT }] as const;

// Admins only: the caller passes enabled=false for anyone else, so a member sends no request.
export function useUsers(enabled: boolean) {
  return useQuery({
    queryKey: usersKey,
    enabled,
    queryFn: () => unwrap(api.GET("/v1/users", { params: { query: { limit: USERS_LIMIT } } })),
  });
}

export function useCreateUser() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: UserValues) => unwrap(api.POST("/v1/users", { body })),
    onSettled: () => client.invalidateQueries({ queryKey: ["users"] }),
  });
}
