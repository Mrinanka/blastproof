import { connection } from "next/server";

import { LoginForm } from "@/components/auth/login-form";

export const metadata = { title: "Sign in · Blastproof" };

export default async function LoginPage({ searchParams }: PageProps<"/login">) {
  // Render per request so Next can put the proxy's nonce on its scripts.
  await connection();
  const { next } = await searchParams;
  return (
    <main className="flex flex-1 items-center justify-center p-6">
      <LoginForm next={typeof next === "string" ? next : undefined} />
    </main>
  );
}
