import { Users } from "@/components/users/users";

export const metadata = { title: "Users" };

export default function Page() {
  return (
    <div className="space-y-4 p-6">
      <div>
        <h1 className="text-xl font-semibold">Users</h1>
        <p className="text-sm text-muted-foreground">Who can sign in to the dashboard.</p>
      </div>
      <Users />
    </div>
  );
}
