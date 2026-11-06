"use client";

import { createContext, type ReactNode, useContext } from "react";

import type { CurrentUser } from "@/lib/auth";

const AuthContext = createContext<CurrentUser | null>(null);

export function AuthProvider({ user, children }: { user: CurrentUser; children: ReactNode }) {
  return <AuthContext.Provider value={user}>{children}</AuthContext.Provider>;
}

/** Текущий пользователь внутри AuthGate. Снаружи — ошибка программиста. */
export function useCurrentUser(): CurrentUser {
  const user = useContext(AuthContext);
  if (!user) throw new Error("useCurrentUser вызывается только внутри AuthGate");
  return user;
}
