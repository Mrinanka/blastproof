import { ChangeRequest } from "@/components/change-request/change-request";

export const metadata = { title: "Change request" };

export default async function Page(props: PageProps<"/dashboard/actions/[id]">) {
  const { id } = await props.params;
  return (
    <div className="space-y-4 p-6">
      <h1 className="text-xl font-semibold">Change request</h1>
      <ChangeRequest id={id} />
    </div>
  );
}
