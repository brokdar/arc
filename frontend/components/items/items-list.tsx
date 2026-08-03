"use client";

import { $api } from "@/lib/api/client";

/**
 * Example component demonstrating the typed API client — the query key,
 * path, and response shape all come from the generated OpenAPI types.
 * Delete alongside the backend items domain.
 */
export function ItemsList() {
  const { data, isPending, error } = $api.useQuery("get", "/api/v1/items");

  if (isPending) {
    return <p className="text-muted-foreground">Loading items…</p>;
  }
  if (error) {
    return <p className="text-destructive">Failed to load items.</p>;
  }
  if (data.items.length === 0) {
    return <p className="text-muted-foreground">No items yet.</p>;
  }

  return (
    <ul className="space-y-2">
      {data.items.map((item) => (
        <li key={item.id} className="rounded-md border p-3">
          <span className="font-medium">{item.name}</span>
          {item.description ? (
            <span className="text-muted-foreground"> — {item.description}</span>
          ) : null}
        </li>
      ))}
    </ul>
  );
}
