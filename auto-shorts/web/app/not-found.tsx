import Link from "next/link";

export default function NotFound() {
  return (
    <main className="flex min-h-dvh flex-col items-center justify-center gap-3 bg-bg p-6 text-center">
      <p className="font-mono text-2xs tracking-[0.2em] text-fg-muted uppercase">404 · нет сигнала</p>
      <h1 className="text-xl font-semibold">Такой страницы нет</h1>
      <p className="max-w-sm text-sm text-fg-muted">Проверьте адрес или вернитесь на пульт.</p>
      <Link href="/" className="text-sm text-accent hover:underline">
        На главную
      </Link>
    </main>
  );
}
