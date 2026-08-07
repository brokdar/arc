import { redirect } from "next/navigation";

/**
 * The root has no content of its own: a single athlete opening the app wants
 * the week (D60). `/calendar` is behind the same guard, so an unauthenticated
 * visitor still ends up at `/login`, one hop later.
 */
export default function Home() {
  redirect("/calendar");
}
