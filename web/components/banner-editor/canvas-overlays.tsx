"use client";

import type { Schemas } from "@/lib/api/client";
import {
  CANVAS_H,
  CANVAS_W,
  deadZoneLabel,
  type PlatformView,
  rectToBox,
  type SafeZoneProfile,
} from "@/lib/safe-zones";

export type CanvasBackground = "checker" | "sample" | "black";

/** Узоры холста: шахматка (прозрачность креатива видна), штриховка мёртвых зон, «образец» кадра. */
export function CanvasDefs({ idPrefix }: { idPrefix: string }) {
  return (
    <defs>
      <pattern id={`${idPrefix}-checker`} width="80" height="80" patternUnits="userSpaceOnUse">
        <rect width="80" height="80" fill="#2b2d31" />
        <rect width="40" height="40" fill="#3a3d42" />
        <rect x="40" y="40" width="40" height="40" fill="#3a3d42" />
      </pattern>
      <pattern
        id={`${idPrefix}-hatch`}
        width="28"
        height="28"
        patternUnits="userSpaceOnUse"
        patternTransform="rotate(45)"
      >
        <rect width="28" height="28" fill="var(--warning)" fillOpacity="0.1" />
        <line x1="0" y1="0" x2="0" y2="28" stroke="var(--warning)" strokeOpacity="0.45" strokeWidth="8" />
      </pattern>
      <linearGradient id={`${idPrefix}-sample`} x1="0" y1="0" x2="0.35" y2="1">
        <stop offset="0" stopColor="#1d3b4a" />
        <stop offset="0.55" stopColor="#2c2446" />
        <stop offset="1" stopColor="#141418" />
      </linearGradient>
      <radialGradient id={`${idPrefix}-sample-glow`} cx="0.5" cy="0.38" r="0.45">
        <stop offset="0" stopColor="#f2c14e" stopOpacity="0.35" />
        <stop offset="1" stopColor="#f2c14e" stopOpacity="0" />
      </radialGradient>
    </defs>
  );
}

export function CanvasBackdrop({ kind, idPrefix }: { kind: CanvasBackground; idPrefix: string }) {
  if (kind === "black") return <rect width={CANVAS_W} height={CANVAS_H} fill="#000" />;
  if (kind === "checker") return <rect width={CANVAS_W} height={CANVAS_H} fill={`url(#${idPrefix}-checker)`} />;
  return (
    <g aria-hidden="true">
      <rect width={CANVAS_W} height={CANVAS_H} fill={`url(#${idPrefix}-sample)`} />
      <rect width={CANVAS_W} height={CANVAS_H} fill={`url(#${idPrefix}-sample-glow)`} />
      {/* Условный «герой» кадра: силуэт, чтобы было видно, что баннер перекрывает. */}
      <circle cx="540" cy="640" r="150" fill="#ffffff" fillOpacity="0.07" />
      <path d="M300 1100 Q540 760 780 1100 L820 1300 L260 1300 Z" fill="#ffffff" fillOpacity="0.06" />
    </g>
  );
}

const LABEL_SIZE = 26;

/** Мёртвые зоны профиля (штриховка), безопасная зона (циан), зона оффера и дополнительный профиль. */
export function SafeZoneOverlay({
  profile,
  secondary,
  idPrefix,
}: {
  profile: SafeZoneProfile;
  secondary?: SafeZoneProfile;
  idPrefix: string;
}) {
  const safe = rectToBox(profile.safe);
  const offer = profile.offer_area ? rectToBox(profile.offer_area) : null;
  const secondarySafe = secondary && secondary.id !== profile.id ? rectToBox(secondary.safe) : null;
  return (
    <g pointerEvents="none" data-testid="safe-zone-overlay" data-profile={profile.id}>
      {profile.dead_zones.map((zone) => {
        const box = rectToBox(zone);
        if (box.w <= 0 || box.h <= 0) return null;
        const vertical = box.h > box.w * 2;
        return (
          <g key={zone.id}>
            <rect x={box.x} y={box.y} width={box.w} height={box.h} fill={`url(#${idPrefix}-hatch)`} />
            {vertical ? (
              <text
                x={box.x + box.w / 2}
                y={box.y + box.h / 2}
                transform={`rotate(90 ${box.x + box.w / 2} ${box.y + box.h / 2})`}
                textAnchor="middle"
                dominantBaseline="central"
                fontSize={LABEL_SIZE}
                fill="var(--warning)"
                fontFamily="var(--font-mono)"
              >
                {deadZoneLabel(zone.id)}
              </text>
            ) : box.h >= LABEL_SIZE * 1.6 ? (
              <text
                x={box.x + box.w / 2}
                y={box.y + box.h / 2}
                textAnchor="middle"
                dominantBaseline="central"
                fontSize={LABEL_SIZE}
                fill="var(--warning)"
                fontFamily="var(--font-mono)"
              >
                {deadZoneLabel(zone.id)}
              </text>
            ) : null}
          </g>
        );
      })}
      {secondarySafe ? (
        <rect
          x={secondarySafe.x}
          y={secondarySafe.y}
          width={secondarySafe.w}
          height={secondarySafe.h}
          fill="none"
          stroke="var(--fg-muted)"
          strokeOpacity="0.8"
          strokeDasharray="4 6"
          strokeWidth="1.5"
          vectorEffect="non-scaling-stroke"
        />
      ) : null}
      {offer ? (
        <g>
          <rect
            x={offer.x}
            y={offer.y}
            width={offer.w}
            height={offer.h}
            fill="var(--accent)"
            fillOpacity="0.05"
            stroke="var(--accent)"
            strokeOpacity="0.55"
            strokeDasharray="2 5"
            strokeWidth="1"
            vectorEffect="non-scaling-stroke"
          />
          <text
            x={offer.x + 10}
            y={offer.y - 10}
            fontSize={22}
            fill="var(--accent)"
            fillOpacity="0.8"
            fontFamily="var(--font-mono)"
          >
            зона оффера
          </text>
        </g>
      ) : null}
      <rect
        x={safe.x}
        y={safe.y}
        width={safe.w}
        height={safe.h}
        fill="none"
        stroke="var(--accent)"
        strokeDasharray="10 6"
        strokeWidth="1.5"
        vectorEffect="non-scaling-stroke"
      />
      <SafeCorners box={safe} />
      {/* Подпись — в правом верхнем углу зоны: слева сверху обычно стоит микро-бренд. */}
      <text
        x={safe.x + safe.w - 10}
        y={safe.y + 30}
        textAnchor="end"
        fontSize={24}
        fill="var(--accent)"
        fontFamily="var(--font-mono)"
      >
        безопасная зона · {profile.label}
      </text>
    </g>
  );
}

/** Уголки title-safe рамки, как на мониторе аппаратной. */
function SafeCorners({ box }: { box: { x: number; y: number; w: number; h: number } }) {
  const l = 36;
  const x2 = box.x + box.w;
  const y2 = box.y + box.h;
  const d = [
    `M${box.x} ${box.y + l} V${box.y} H${box.x + l}`,
    `M${x2 - l} ${box.y} H${x2} V${box.y + l}`,
    `M${x2} ${y2 - l} V${y2} H${x2 - l}`,
    `M${box.x + l} ${y2} H${box.x} V${y2 - l}`,
  ].join(" ");
  return <path d={d} fill="none" stroke="var(--accent)" strokeWidth="3" vectorEffect="non-scaling-stroke" />;
}

export function CaptionsZoneOverlay({ zone }: { zone: Schemas["CaptionsZoneInfo"] }) {
  const box = rectToBox(zone.rect);
  if (box.w <= 0 || box.h <= 0) return null;
  const color = zone.fits ? "var(--machine)" : "var(--negative)";
  return (
    <g pointerEvents="none" data-testid="captions-zone">
      <rect
        x={box.x}
        y={box.y}
        width={box.w}
        height={box.h}
        fill={color}
        fillOpacity="0.1"
        stroke={color}
        strokeDasharray="8 6"
        strokeWidth="1.5"
        vectorEffect="non-scaling-stroke"
      />
      <text x={box.x + 12} y={box.y + 32} fontSize={24} fill={color} fontFamily="var(--font-mono)">
        {zone.fits ? "зона субтитров" : "зона субтитров — мало места"}
      </text>
      {/* Две условные строки субтитров. */}
      <rect
        x={box.x + box.w * 0.15}
        y={box.y + box.h * 0.42}
        width={box.w * 0.7}
        height={30}
        rx={8}
        fill={color}
        fillOpacity="0.18"
      />
      <rect
        x={box.x + box.w * 0.25}
        y={box.y + box.h * 0.42 + 44}
        width={box.w * 0.5}
        height={30}
        rx={8}
        fill={color}
        fillOpacity="0.18"
      />
    </g>
  );
}

/** Силуэты интерфейса площадки: колонка лайков, подпись, кнопки. Только для ориентира. */
export function PlatformUiSilhouette({ view, profile }: { view: PlatformView; profile: SafeZoneProfile }) {
  const safe = rectToBox(profile.safe);
  const bottomTop = safe.y + safe.h;
  const railX = view === "vk" ? 1000 : Math.min(1010, safe.x + safe.w + (CANVAS_W - safe.x - safe.w) / 2);
  const icons = view === "vk" ? 4 : 5;
  const fill = "#ffffff";
  return (
    <g pointerEvents="none" aria-hidden="true" opacity="0.32">
      {/* верхняя панель */}
      <rect x={40} y={70} width={120} height={36} rx={18} fill={fill} fillOpacity="0.5" />
      <circle cx={CANVAS_W - 150} cy={88} r={22} fill={fill} fillOpacity="0.5" />
      <circle cx={CANVAS_W - 70} cy={88} r={22} fill={fill} fillOpacity="0.5" />
      {/* колонка действий */}
      {Array.from({ length: icons }).map((_, i) => (
        <g key={i}>
          <circle cx={railX} cy={bottomTop - 420 + i * 150} r={40} fill={fill} fillOpacity="0.55" />
          <rect
            x={railX - 30}
            y={bottomTop - 365 + i * 150}
            width={60}
            height={14}
            rx={7}
            fill={fill}
            fillOpacity="0.45"
          />
        </g>
      ))}
      {/* подпись: канал, название, звук */}
      <circle cx={100} cy={bottomTop + 130} r={34} fill={fill} fillOpacity="0.55" />
      <rect x={150} y={bottomTop + 114} width={260} height={30} rx={8} fill={fill} fillOpacity="0.55" />
      <rect x={430} y={bottomTop + 110} width={150} height={40} rx={20} fill={fill} fillOpacity="0.4" />
      <rect x={66} y={bottomTop + 190} width={720} height={28} rx={8} fill={fill} fillOpacity="0.45" />
      <rect x={66} y={bottomTop + 232} width={520} height={28} rx={8} fill={fill} fillOpacity="0.35" />
      <rect x={66} y={bottomTop + 290} width={420} height={24} rx={8} fill={fill} fillOpacity="0.3" />
      {/* прогресс */}
      <rect x={0} y={CANVAS_H - 12} width={CANVAS_W} height={6} fill={fill} fillOpacity="0.35" />
      <rect x={0} y={CANVAS_H - 12} width={CANVAS_W * 0.42} height={6} fill="#ff3b3b" fillOpacity="0.8" />
    </g>
  );
}
