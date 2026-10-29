import { z } from "zod";

import type { Schemas } from "@/lib/api/client";

export type Agent = Schemas["AgentView"];
export type CreatedAgent = Schemas["AgentCreated"];

// Same bounds as the backend's AgentName (strip, then 1 to 100 characters); the backend stays the authority.
export const agentSchema = z.object({
  name: z
    .string()
    .trim()
    .min(1, "Give the agent a name.")
    .max(100, "Keep the name to 100 characters or fewer."),
});

export type AgentValues = z.infer<typeof agentSchema>;

// Where the README explains the hook, which the dashboard cannot install for the user.
export const HOOK_STEPS_URL = "https://github.com/Mrinanka/blastproof#connect-claude-code";
