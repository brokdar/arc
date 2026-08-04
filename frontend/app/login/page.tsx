import { LoginForm } from "@/components/auth/login-form";

export const metadata = {
  title: "Sign in — arc",
};

export default function LoginPage() {
  return (
    <main className="flex flex-1 items-center justify-center p-8">
      <LoginForm />
    </main>
  );
}
