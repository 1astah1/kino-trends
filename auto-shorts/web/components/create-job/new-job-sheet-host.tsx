"use client";

import dynamic from "next/dynamic";
import { useEffect, useId } from "react";

import { useNewJobSheet } from "./new-job-store";

// Форма грузится при первом открытии: в стартовый бандл оболочки она не входит (§7.1 бюджет бандла).
const NewJobSheet = dynamic(() => import("./new-job-sheet").then((mod) => mod.NewJobSheet), { ssr: false });

/**
 * Точка монтирования NewJobSheet. Можно ставить и в AppShell (для `N` из шапки), и на экран
 * Конвейера: рисует только первый смонтированный хост.
 */
export function NewJobSheetHost() {
  const id = useId();
  const register = useNewJobSheet((state) => state.register);
  const unregister = useNewJobSheet((state) => state.unregister);
  const primary = useNewJobSheet((state) => state.hosts[0] === id);
  const open = useNewJobSheet((state) => state.open);
  const openCount = useNewJobSheet((state) => state.openCount);

  useEffect(() => {
    register(id);
    return () => unregister(id);
  }, [id, register, unregister]);

  if (!primary || openCount === 0) return null;
  return <NewJobSheet key={openCount} open={open} />;
}
