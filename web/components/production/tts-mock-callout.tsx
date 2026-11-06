"use client";

import Link from "next/link";

import { Callout } from "@/components/ui";
import { routeHref } from "@/lib/nav";
import { useOpenAlerts } from "@/lib/queries/alerts";
import { defaultTtsIsMock, useProviderOptions } from "@/lib/queries/jobs";

/**
 * Красная плашка «TTS в mock» (§7.3 «Конвейер»; §10.2 M0: в прод не должен уходить синус вместо голоса).
 * Источники: провайдер озвучки по умолчанию — заглушка, или открыт алерт `tts_mock_in_prod`.
 */
export function TtsMockCallout() {
  const providers = useProviderOptions();
  const alerts = useOpenAlerts();
  const mock = defaultTtsIsMock(providers.data);
  const alert = alerts.data?.find((item) => item.code === "tts_mock_in_prod");
  if (!mock && !alert) return null;
  return (
    <Callout
      variant="negative"
      title="Озвучка в режиме заглушки"
      action={
        <Link href={routeHref("/settings", { tab: "providers" })} className="text-xs text-accent hover:underline">
          Провайдеры
        </Link>
      }
    >
      {mock ? (
        <>
          Провайдер озвучки по умолчанию — <span className="font-mono">{mock.label}</span>: ролики получат тон-заглушку
          вместо голоса. Включите настоящий TTS до запуска роликов.
        </>
      ) : (
        (alert?.title ?? "Воркер сообщил о TTS-заглушке в проде.")
      )}
    </Callout>
  );
}
