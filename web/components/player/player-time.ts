/**
 * Время плеера в отдельном маленьком сторе: оверлей, дорожка и караоке подписываются только на него,
 * и экран приёмки целиком не перерисовывается 10 раз в секунду.
 */
import { createStore, type StoreApi } from "zustand/vanilla";

export interface PlayerTimeState {
  /** Текущая позиция, секунды. */
  t: number;
  /** Длительность из метаданных видео (или из API, пока метаданных нет). */
  duration: number;
  playing: boolean;
}

export type PlayerTimeStore = StoreApi<PlayerTimeState>;

export function createPlayerTimeStore(): PlayerTimeStore {
  return createStore<PlayerTimeState>(() => ({ t: 0, duration: 0, playing: false }));
}

/** Команды плеера для клавиш и дорожки. */
export interface PlayerController {
  togglePlay: () => void;
  play: () => void;
  pause: () => void;
  seekBy: (deltaSec: number) => void;
  seekTo: (sec: number) => void;
  frame: (delta: 1 | -1) => void;
  fullscreen: () => void;
}

/** Шаг покадровой перемотки: превью и финал — 30 fps. */
export const FRAME_SEC = 1 / 30;
