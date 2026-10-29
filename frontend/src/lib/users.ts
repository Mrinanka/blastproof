import { z } from "zod";

import type { Schemas } from "@/lib/api/client";

export type Account = Schemas["AccountView"];

// Same shapes as the backend's CreateUserRequest; its 422 and 409 messages still win when shown.
export const userSchema = z.object({
  email: z
    .string()
    .trim()
    .max(320, "That email is too long.")
    .regex(/^[^@\s]+@[^@\s]+\.[^@\s]+$/, "Enter a valid email address."),
  display_name: z
    .string()
    .trim()
    .min(1, "Give the user a display name.")
    .max(100, "Keep the display name to 100 characters or fewer."),
  // Not trimmed: spaces are part of a password.
  password: z
    .string()
    .min(12, "The password needs at least 12 characters.")
    .max(256, "The password can be at most 256 characters."),
  role: z.enum(["admin", "member"]),
});

export type UserValues = z.infer<typeof userSchema>;
