import type { Metadata } from "next";

import { PageSuspense } from "@/components/common/page-suspense";
import { LoginScreen } from "@/components/auth/login-screen";

export const metadata: Metadata = { title: "Вход" };

export default function LoginPage() {
  return (
    <PageSuspense fallback={<div className="h-dvh bg-bg" />}>
      <LoginScreen />
    </PageSuspense>
  );
}
