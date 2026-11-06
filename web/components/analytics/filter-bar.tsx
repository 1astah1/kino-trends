"use client";

import { Button, DateRangeInput, PillToggle, Select } from "@/components/ui";
import { useChannels, useNicheOptions } from "@/lib/queries/channels";
import { useProducts } from "@/lib/queries/products";

import { COHORT_AGE_LABELS, PLATFORM_LABELS } from "./labels";
import { type AnalyticsFilterState, RANGE_PRESETS } from "./use-analytics-filters";

const COHORT_AGES = ["h24", "h72", "d7", "d14", "d30", "lifetime"] as const;

/** Строка фильтров аналитики: период с пресетами, площадка, канал, ниша, продукт, база и режим. */
export function AnalyticsFilterBar({ state }: { state: AnalyticsFilterState }) {
  const channels = useChannels({ platform: state.platform ?? undefined });
  const niches = useNicheOptions();
  const products = useProducts({ status: "all" });

  return (
    <div className="flex flex-col gap-2" data-testid="analytics-filters">
      <div className="flex flex-wrap items-center gap-2">
        <DateRangeInput
          from={state.dateFrom}
          to={state.dateTo}
          onFromChange={state.setDateFrom}
          onToChange={state.setDateTo}
          toMax={state.today}
        />
        <div role="group" aria-label="Быстрый период" className="flex items-center gap-1">
          {RANGE_PRESETS.map((days) => (
            <Button
              key={days}
              size="xs"
              variant={state.activePreset === days ? "default" : "outline"}
              aria-pressed={state.activePreset === days}
              onClick={() => state.setPreset(days)}
            >
              {days} дн
            </Button>
          ))}
        </div>
        <PillToggle
          label="База просмотров"
          options={[
            { value: "public", label: "Публичные" },
            { value: "engaged", label: "Вовлечённые" },
          ]}
          value={state.basis}
          onChange={(value) => void state.setBasis(value)}
        />
        <PillToggle
          label="Режим"
          options={[
            { value: "activity", label: "Активность" },
            { value: "cohort", label: "Когорта" },
          ]}
          value={state.mode}
          onChange={(value) => void state.setMode(value)}
        />
        {state.mode === "cohort" ? (
          <Select
            aria-label="Возраст когорты"
            className="h-7 w-36 text-xs"
            value={state.cohortAge}
            onChange={(event) => void state.setCohortAge(event.target.value as (typeof COHORT_AGES)[number])}
          >
            {COHORT_AGES.map((age) => (
              <option key={age} value={age}>
                {COHORT_AGE_LABELS[age]}
              </option>
            ))}
          </Select>
        ) : null}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Select
          aria-label="Площадка"
          className="h-7 w-36 text-xs"
          value={state.platform ?? ""}
          onChange={(event) => {
            const value = event.target.value;
            void state.setPlatform(value === "youtube" || value === "vk" ? value : null);
            // Канал другой площадки в фильтре не имеет смысла.
            if (state.channelId) void state.setChannelId(null);
          }}
        >
          <option value="">Все площадки</option>
          {(["youtube", "vk"] as const).map((platform) => (
            <option key={platform} value={platform}>
              {PLATFORM_LABELS[platform]}
            </option>
          ))}
        </Select>
        <Select
          aria-label="Канал"
          className="h-7 w-56 text-xs"
          value={state.channelId ?? ""}
          onChange={(event) => void state.setChannelId(event.target.value || null)}
        >
          <option value="">Все каналы</option>
          {(channels.data?.items ?? []).map((channel) => (
            <option key={channel.id} value={channel.id}>
              {channel.title || channel.id}
            </option>
          ))}
          {state.channelId && !(channels.data?.items ?? []).some((channel) => channel.id === state.channelId) ? (
            <option value={state.channelId}>{state.channelId}</option>
          ) : null}
        </Select>
        <Select
          aria-label="Ниша"
          className="h-7 w-48 text-xs"
          value={state.nicheId ?? ""}
          onChange={(event) => void state.setNicheId(event.target.value || null)}
        >
          <option value="">Все ниши</option>
          {(niches.data ?? []).map((niche) => (
            <option key={niche.id} value={niche.id}>
              {niche.label || niche.id}
            </option>
          ))}
        </Select>
        <Select
          aria-label="Продукт"
          className="h-7 w-48 text-xs"
          value={state.productId ?? ""}
          onChange={(event) => void state.setProductId(event.target.value || null)}
        >
          <option value="">Все продукты</option>
          {(products.data?.items ?? []).map((product) => (
            <option key={product.id} value={product.id}>
              {product.name || product.id}
            </option>
          ))}
        </Select>
      </div>
    </div>
  );
}
