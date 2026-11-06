// Источник: retentionos/packages/ui/components/button.tsx (скопировано 26.09.2026),
// адаптировано под токены «Аппаратная» (§7.6): высоты 32/28 px, основная кнопка — циан.
import { type VariantProps, cva } from "class-variance-authority";
import type { ButtonHTMLAttributes } from "react";

import { cn } from "@/lib/cn";

export const buttonVariants = cva(
  "inline-flex shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-md text-sm font-medium transition-colors duration-150 disabled:pointer-events-none disabled:opacity-50 aria-disabled:opacity-60",
  {
    variants: {
      variant: {
        default: "bg-accent text-accent-fg hover:bg-accent/90",
        human: "bg-human text-human-fg hover:bg-human/90",
        outline: "border border-border bg-transparent text-fg hover:bg-surface-2",
        ghost: "text-fg hover:bg-surface-2",
        destructive: "border border-negative/40 bg-negative/10 text-negative hover:bg-negative/20",
      },
      size: {
        default: "h-8 px-3",
        sm: "h-7 px-2.5 text-xs",
        xs: "h-6 px-2 text-xs",
        icon: "h-8 w-8",
        "icon-sm": "h-7 w-7",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  },
);

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {}

export function Button({ className, variant, size, type = "button", ...props }: ButtonProps) {
  return <button type={type} className={cn(buttonVariants({ variant, size }), className)} {...props} />;
}
