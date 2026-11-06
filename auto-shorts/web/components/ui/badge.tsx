// Источник: retentionos/packages/ui/components/badge.tsx (скопировано 26.09.2026).
// Варианты переведены на смысловые токены §7.6: human — «твой ход», machine — «работает машина».
import { type VariantProps, cva } from "class-variance-authority";
import type { HTMLAttributes } from "react";

import { cn } from "@/lib/cn";

export const badgeVariants = cva(
  "inline-flex items-center gap-1 rounded-sm px-1.5 py-px text-xs font-medium leading-4",
  {
    variants: {
      variant: {
        neutral: "bg-surface-2 text-fg-muted",
        accent: "bg-accent/15 text-accent",
        human: "bg-human/15 text-human",
        machine: "bg-machine/15 text-machine",
        positive: "bg-positive/15 text-positive",
        warning: "bg-warning/15 text-warning",
        negative: "bg-negative/15 text-negative",
      },
      outline: {
        true: "border",
        false: "",
      },
    },
    defaultVariants: {
      variant: "neutral",
      outline: false,
    },
    compoundVariants: [
      { variant: "neutral", outline: true, class: "border-border" },
      { variant: "accent", outline: true, class: "border-accent/40" },
      { variant: "human", outline: true, class: "border-human/40" },
      { variant: "machine", outline: true, class: "border-machine/40" },
      { variant: "positive", outline: true, class: "border-positive/40" },
      { variant: "warning", outline: true, class: "border-warning/40" },
      { variant: "negative", outline: true, class: "border-negative/40" },
    ],
  },
);

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, outline, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant, outline }), className)} {...props} />;
}
