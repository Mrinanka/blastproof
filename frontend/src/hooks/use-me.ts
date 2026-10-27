import { useQuery } from "@tanstack/react-query";

import { api, type Schemas } from "@/lib/api/client";
import { unwrap } from "@/lib/api/errors";

export function useMe() {
  return useQuery<Schemas["UserView"]>({
    queryKey: ["me"],
    queryFn: () => unwrap(api.GET("/v1/auth/me")),
  });
}
