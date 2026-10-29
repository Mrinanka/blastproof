// F11 guards: validation bounds and rule text (FE-24, FE-25, FE-26).
import { describe, expect, it } from "vitest";

import { agentSchema } from "../src/lib/agents";
import { conditionText, passLabel } from "../src/lib/rules";
import { userSchema } from "../src/lib/users";

const user = (over: Record<string, unknown> = {}) => ({
  email: "a@b.co",
  display_name: "Ann",
  password: "x".repeat(12),
  role: "member",
  ...over,
});

describe("agentSchema", () => {
  it("trims and bounds the name", () => {
    expect(agentSchema.parse({ name: "  bot  " }).name).toBe("bot");
    expect(agentSchema.safeParse({ name: "   " }).success).toBe(false);
    expect(agentSchema.safeParse({ name: "a".repeat(100) }).success).toBe(true);
    expect(agentSchema.safeParse({ name: "a".repeat(101) }).success).toBe(false);
  });
});

describe("userSchema", () => {
  it.each(["a@b.co", "x.y+z@sub.example.org"])("accepts %s", (email) => {
    expect(userSchema.safeParse(user({ email })).success).toBe(true);
  });
  it.each(["", "a", "a@b", "a b@c.d", "@b.co"])("rejects the email %j", (email) => {
    expect(userSchema.safeParse(user({ email })).success).toBe(false);
  });
  it("bounds the password at 12 to 256 and does not trim it", () => {
    const ok = (n: number) => userSchema.safeParse(user({ password: "x".repeat(n) })).success;
    expect([ok(11), ok(12), ok(256), ok(257)]).toEqual([false, true, true, false]);
    const padded = "  " + "x".repeat(10) + "  ";
    expect(userSchema.parse(user({ password: padded })).password).toBe(padded);
  });
  it("bounds the display name and limits the role", () => {
    expect(userSchema.safeParse(user({ display_name: " " })).success).toBe(false);
    expect(userSchema.safeParse(user({ display_name: "a".repeat(101) })).success).toBe(false);
    expect(userSchema.safeParse(user({ role: "root" })).success).toBe(false);
  });
});

describe("rules text", () => {
  it("spells conditions the way the rules file does", () => {
    expect(conditionText("statement_in", ["delete", "update"])).toBe(
      "statement_in: delete, update",
    );
    expect(conditionText("has_where", false)).toBe("has_where: false");
    expect(conditionText("fraction_of_table_gt", 0.5)).toBe("fraction_of_table_gt: 0.5");
    expect(conditionText("x", { a: 1 })).toBe('x: {"a":1}');
  });
  it("labels the passes", () => {
    expect(passLabel("pre")).toBe("Before preview");
    expect(passLabel("post")).toBe("After preview");
  });
});
