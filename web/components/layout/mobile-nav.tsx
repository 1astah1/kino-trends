"use client";

import { X } from "lucide-react";
import { Dialog as RadixDialog } from "radix-ui";

import { Button } from "@/components/ui";
import { useShellStore } from "@/lib/store/shell";

import { Brand } from "./brand";
import { NavList } from "./nav-list";

/** Меню на телефоне: выезжающая панель с тем же NavList. */
export function MobileNav() {
  const open = useShellStore((state) => state.mobileNavOpen);
  const setOpen = useShellStore((state) => state.setMobileNavOpen);
  return (
    <RadixDialog.Root open={open} onOpenChange={setOpen}>
      <RadixDialog.Portal>
        <RadixDialog.Overlay className="fixed inset-0 z-40 bg-overlay md:hidden" />
        <RadixDialog.Content className="fixed inset-y-0 left-0 z-50 flex w-[264px] max-w-[85vw] flex-col border-r border-border bg-surface md:hidden">
          <RadixDialog.Title className="sr-only">Меню</RadixDialog.Title>
          <RadixDialog.Description className="sr-only">Разделы консоли</RadixDialog.Description>
          <div className="flex items-center justify-between border-b border-border pr-2">
            <Brand bordered={false} />
            <RadixDialog.Close asChild>
              <Button variant="ghost" size="icon-sm" aria-label="Закрыть меню">
                <X className="h-4 w-4" aria-hidden="true" />
              </Button>
            </RadixDialog.Close>
          </div>
          <NavList onNavigate={() => setOpen(false)} />
        </RadixDialog.Content>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  );
}
