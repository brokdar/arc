import { ItemsList } from "@/components/items/items-list";

export default function ItemsPage() {
  return (
    <main className="mx-auto max-w-2xl space-y-6 p-8">
      <h1 className="font-semibold text-2xl">Items</h1>
      <ItemsList />
    </main>
  );
}
