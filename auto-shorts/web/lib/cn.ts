// Источник: retentionos/packages/ui/lib/cn.ts (скопировано 26.09.2026).
import { type ClassValue, clsx } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";

// Своя шкала текста (text-2xs) и токены цветов: tailwind-merge должен знать, что
// `text-2xs` — размер, а не цвет, иначе `text-2xs text-fg-muted` схлопнется в одно.
const twMerge = extendTailwindMerge({
  extend: {
    theme: {
      text: ["2xs"],
    },
  },
});

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
