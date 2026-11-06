/**
 * Web Share на телефоне (§7.3 «Пакеты»): отправить сам файл ролика в приложение площадки.
 *
 * `navigator.share` требует свежего жеста пользователя, а файл весит мегабайты — поэтому в два шага:
 * «Подготовить» качает файл (`GET /packages/{id}/video`, тот же origin; сервер отметит «скачан»),
 * «Поделиться» отдаёт его системе. Где файлы делиться нельзя — делимся только текстом.
 */
type ShareNavigator = Navigator & {
  share?: (data: ShareData) => Promise<void>;
  canShare?: (data?: ShareData) => boolean;
};

function nav(): ShareNavigator | null {
  return typeof navigator === "undefined" ? null : (navigator as ShareNavigator);
}

export function canWebShare(): boolean {
  return typeof nav()?.share === "function";
}

/** Поддерживает ли браузер отправку файлов (проверка на пустом mp4 — без скачивания). */
export function canShareFiles(): boolean {
  const n = nav();
  if (!n?.canShare || typeof File === "undefined") return false;
  try {
    return n.canShare({ files: [new File([new Uint8Array(0)], "probe.mp4", { type: "video/mp4" })] });
  } catch {
    return false;
  }
}

export async function fetchPackageFile(downloadUrl: string, filename: string, signal?: AbortSignal): Promise<File> {
  const response = await fetch(downloadUrl, { credentials: "include", signal });
  if (!response.ok) throw new Error(`Файл не скачался (HTTP ${response.status})`);
  const blob = await response.blob();
  return new File([blob], filename, { type: blob.type || "video/mp4" });
}

/** `true` — отправлено, `false` — оператор закрыл системное окно. */
export async function shareNow(data: { title: string; text: string; file?: File | null }): Promise<boolean> {
  const n = nav();
  if (!n?.share) throw new Error("Браузер не умеет «Поделиться»");
  const payload: ShareData = data.file
    ? { files: [data.file], title: data.title, text: data.text }
    : { title: data.title, text: data.text };
  try {
    await n.share(payload);
    return true;
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") return false;
    throw error;
  }
}
