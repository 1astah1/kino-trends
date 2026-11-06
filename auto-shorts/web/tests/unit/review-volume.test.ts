import { expect, test } from "@playwright/test";

import { reviewKeyAction } from "@/components/review/review-logic";
import { clampVolume, useReviewPrefs } from "@/lib/store/review-prefs";

const key = (code: string, extra: Partial<{ shiftKey: boolean; ctrlKey: boolean }> = {}) => ({
  code,
  key: "",
  shiftKey: false,
  ctrlKey: false,
  metaKey: false,
  altKey: false,
  ...extra,
});

test.describe("громкость в приёмке", () => {
  test.beforeEach(() => {
    useReviewPrefs.setState({ muted: false, volume: 1 });
  });

  test("стрелки вверх и вниз меняют громкость на 10%, с Ctrl не перехватываются", () => {
    expect(reviewKeyAction(key("ArrowUp"), "idle")).toEqual({ type: "volume", delta: 0.1 });
    expect(reviewKeyAction(key("ArrowDown"), "idle")).toEqual({ type: "volume", delta: -0.1 });
    expect(reviewKeyAction(key("ArrowUp", { ctrlKey: true }), "idle")).toBeNull();
    expect(reviewKeyAction(key("KeyM"), "idle")).toEqual({ type: "toggle_mute" });
  });

  test("громкость держится в пределах 0–100% с шагом 1%", () => {
    expect(clampVolume(1.4)).toBe(1);
    expect(clampVolume(-0.2)).toBe(0);
    expect(clampVolume(0.333)).toBe(0.33);
    expect(clampVolume(Number.NaN)).toBe(1);
  });

  test("ползунок до нуля = без звука; звук обратно с нуля — половина громкости", () => {
    const prefs = useReviewPrefs.getState;
    prefs().setVolume(0.4);
    expect(prefs()).toMatchObject({ volume: 0.4, muted: false });
    prefs().setVolume(0);
    expect(prefs()).toMatchObject({ volume: 0, muted: true });
    prefs().toggleMuted();
    expect(prefs()).toMatchObject({ volume: 0.5, muted: false });
  });

  test("стрелка вверх при выключенном звуке начинает с нуля и включает звук", () => {
    const prefs = useReviewPrefs.getState;
    prefs().setVolume(0.8);
    prefs().toggleMuted();
    expect(prefs().muted).toBe(true);
    prefs().changeVolume(0.1);
    expect(prefs()).toMatchObject({ volume: 0.1, muted: false });
    prefs().changeVolume(-0.5);
    expect(prefs()).toMatchObject({ volume: 0, muted: true });
  });
});
