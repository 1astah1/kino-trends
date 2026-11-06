"use client";

import { useEffect, useState } from "react";

/** Текущее время, обновляемое по таймеру: «N с назад», свежесть heartbeat, блокировки. */
export function useNow(intervalMs = 15_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}
