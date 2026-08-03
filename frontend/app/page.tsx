import Link from "next/link";

import { Button } from "@/components/ui/button";

export default function Home() {
  return (
    <main className="flex flex-1 flex-col items-center justify-center gap-6 p-8">
      <h1 className="font-semibold text-3xl">__PROJECT_NAME__</h1>
      <p className="max-w-md text-center text-muted-foreground">
        Full-stack starter: FastAPI + Next.js with an end-to-end typed API
        contract. Visit the example page to see the generated client in action.
      </p>
      <Button render={<Link href="/items" />}>View items example</Button>
    </main>
  );
}
