import "@fontsource-variable/onest/index.css";
import "@fontsource-variable/jetbrains-mono/index.css";
import "./globals.css";

import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";

import { THEME_BOOT_SCRIPT } from "@/lib/store/theme";

import { Providers } from "./providers";

export const metadata: Metadata = {
  title: {
    default: "Auto Shorts",
    template: "%s · Auto Shorts",
  },
  description: "Операторская консоль Auto Shorts",
  robots: { index: false, follow: false },
  icons: { icon: "/icon.svg" },
};

export const viewport: Viewport = {
  colorScheme: "dark light",
  themeColor: [
    { media: "(prefers-color-scheme: dark)", color: "#16181c" },
    { media: "(prefers-color-scheme: light)", color: "#f8f9fb" },
  ],
};

/**
 * RootLayout(fonts, data-theme) → Providers (§7.5). Шрифты — @fontsource-variable (кириллица
 * в бандле, `out/_next/static/media`), никаких внешних ресурсов (§7.7).
 */
export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="ru" data-theme="dark" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOT_SCRIPT }} />
      </head>
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
