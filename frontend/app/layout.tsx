import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { ServiceWorkerRegistration } from "@/components/shell/sw-register";
import { Providers } from "./providers";

// Both are variable fonts, so no `weight` list: the whole 100–900 range ships
// in one file and the design system's 400/450/500/600/700 all resolve from it.
const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
  display: "swap",
});

const jetbrainsMono = JetBrains_Mono({
  variable: "--font-jetbrains-mono",
  subsets: ["latin"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "arc",
  description: "Training application for one athlete.",
  // iOS reads these instead of the manifest: the home-screen icon, and a
  // standalone window whose status bar sits over the dark canvas.
  appleWebApp: {
    capable: true,
    title: "arc",
    statusBarStyle: "black-translucent",
  },
  icons: {
    apple: "/icons/apple-touch-icon.png",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    // `dark` is unconditional: arc is a dark-only application. The class
    // is what keeps the `dark:` rules in the vendored shadcn components live.
    <html
      lang="en"
      className={`dark ${inter.variable} ${jetbrainsMono.variable} h-full antialiased`}
    >
      <body className="flex min-h-full flex-col">
        <Providers>{children}</Providers>
        <ServiceWorkerRegistration />
      </body>
    </html>
  );
}
