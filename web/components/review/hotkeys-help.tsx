"use client";

import { Dialog } from "@/components/ui";

const GROUPS: ReadonlyArray<{ title: string; keys: ReadonlyArray<[string, string]> }> = [
  {
    title: "Решение",
    keys: [
      ["A", "одобрить (у ролика с проваленным QC — «вопреки QC», только владелец, A ещё раз — подтвердить)"],
      ["R", "на доработку: затем цифра стадии или Enter — стадия по умолчанию"],
      ["X, затем 1–9, 0", "отклонить с причиной под этой цифрой"],
      ["U", "отменить последнее решение (до 10 минут)"],
      ["Esc", "выйти из выбора причины или стадии"],
    ],
  },
  {
    title: "Очередь",
    keys: [["J / K", "следующий / предыдущий ролик"]],
  },
  {
    title: "Плеер",
    keys: [
      ["Пробел", "пауза / воспроизведение"],
      ["← / →", "на секунду назад / вперёд (с Shift — на 5 секунд)"],
      [", / .", "кадр назад / вперёд"],
      ["M", "звук вкл / выкл"],
      ["↑ / ↓", "громкость громче / тише на 10%"],
      ["L", "повтор ролика по кругу"],
      ["F", "во весь экран"],
    ],
  },
  {
    title: "Оверлей",
    keys: [
      ["S", "мёртвые зоны и безопасная рамка"],
      ["B", "слои баннера и зона субтитров"],
      ["P", "площадка для зон: YouTube ↔ VK"],
      ["?", "эта справка"],
    ],
  },
];

/** Справка по клавишам приёмки (`?`). Клавиши работают в любой раскладке: берётся физическая клавиша. */
export function HotkeysHelp({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Клавиши приёмки"
      description="Работают в любой раскладке. Пока курсор в поле комментария, клавиши молчат."
      className="max-w-xl"
    >
      <div className="grid gap-4 sm:grid-cols-2" data-testid="hotkeys-help">
        {GROUPS.map((group) => (
          <section key={group.title} className="flex flex-col gap-1.5">
            <h3 className="font-mono text-2xs tracking-wider text-fg-muted uppercase">{group.title}</h3>
            <dl className="flex flex-col gap-1">
              {group.keys.map(([key, text]) => (
                <div key={key} className="flex items-baseline gap-2 text-sm">
                  <dt className="shrink-0">
                    <kbd className="rounded-sm border border-border bg-surface-2 px-1.5 font-mono text-xs">{key}</kbd>
                  </dt>
                  <dd className="text-fg-muted">{text}</dd>
                </div>
              ))}
            </dl>
          </section>
        ))}
      </div>
    </Dialog>
  );
}
