"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { KeyRound, Pencil, UserPlus } from "lucide-react";
import { useState } from "react";
import { type FieldValues, type Path, useForm, type UseFormSetError, useWatch } from "react-hook-form";
import { toast } from "sonner";
// zod/mini: tree-shakeable вариант zod 4 — полный `zod` добавлял ~95 КБ gz к странице.
import * as z from "zod/mini";

import { useCurrentUser } from "@/components/auth/auth-context";
import { SectionError } from "@/components/common/section-error";
import {
  Badge,
  Button,
  DataTable,
  type DataTableColumn,
  Dialog,
  FormField,
  Input,
  Select,
  StatusBadge,
} from "@/components/ui";
import { ApiError, ConflictError, errorMessage, ValidationError } from "@/lib/api/errors";
import { formatAgo, formatDateTime } from "@/lib/format";
import { useNow } from "@/lib/hooks/use-now";
import { type User, useCreateUser, useResetPassword, useUpdateUser, useUsers } from "@/lib/queries/users";
import { type Role, ROLE_META, ROLE_OPTIONS } from "@/lib/status-meta";

/** LOGIN_PATTERN из src/autoshorts/api/schemas/common.py. */
const LOGIN_RE = /^[a-z0-9][a-z0-9_.-]{2,31}$/;
const MIN_PASSWORD = 12;
const ROLES = ["admin", "operator", "viewer"] as const satisfies readonly Role[];

const tgField = z.string().check(z.trim(), z.regex(/^\d{0,15}$/, "Только цифры Telegram user id"));
const passwordField = z
  .string()
  .check(z.minLength(MIN_PASSWORD, `Не короче ${MIN_PASSWORD} символов`), z.maxLength(1024, "Слишком длинный пароль"));
const displayNameField = z
  .string()
  .check(z.trim(), z.minLength(1, "Укажите имя"), z.maxLength(100, "Не длиннее 100 символов"));

const createSchema = z
  .object({
    login: z
      .string()
      .check(z.trim(), z.regex(LOGIN_RE, "3–32 символа: a–z, 0–9, точка, дефис, подчёркивание; с буквы или цифры")),
    display_name: displayNameField,
    role: z.enum(ROLES),
    password: passwordField,
    password_confirm: z.string(),
    tg_user_id: tgField,
  })
  .check(
    z.refine((value) => value.password === value.password_confirm, {
      path: ["password_confirm"],
      error: "Пароли не совпадают",
    }),
  );
type CreateValues = z.infer<typeof createSchema>;

const editSchema = z.object({
  display_name: displayNameField,
  role: z.enum(ROLES),
  is_active: z.boolean(),
  tg_user_id: tgField,
});
type EditValues = z.infer<typeof editSchema>;

const resetSchema = z
  .object({
    new_password: passwordField,
    confirm: z.string(),
  })
  .check(
    z.refine((value) => value.new_password === value.confirm, { path: ["confirm"], error: "Пароли не совпадают" }),
  );
type ResetValues = z.infer<typeof resetSchema>;

function tgToNumber(value: string): number | null {
  return value.trim() ? Number(value.trim()) : null;
}

/** 422 → ошибки по полям формы; остальное — в общий текст формы. */
function applyServerErrors<T extends FieldValues>(
  error: unknown,
  setError: UseFormSetError<T>,
  fields: readonly string[],
) {
  if (error instanceof ValidationError) {
    let matched = false;
    for (const [field, message] of Object.entries(error.fieldErrors)) {
      if (fields.includes(field)) {
        setError(field as Path<T>, { type: "server", message });
        matched = true;
      }
    }
    if (matched) return;
  }
  setError("root.server" as Path<T>, { type: "server", message: errorMessage(error) });
}

function UserStatus({ user, now }: { user: User; now: number }) {
  const locked = user.locked_until && Date.parse(user.locked_until) > now;
  if (!user.is_active) return <Badge variant="neutral">Отключён</Badge>;
  if (locked)
    return <Badge variant="warning">Заблокирован до {formatDateTime(user.locked_until, { short: true })}</Badge>;
  return <Badge variant="positive">Активен</Badge>;
}

export function UsersPanel() {
  const me = useCurrentUser();
  const users = useUsers();
  const now = useNow(60_000);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<User | null>(null);
  const [resetting, setResetting] = useState<User | null>(null);

  const columns: Array<DataTableColumn<User>> = [
    {
      key: "login",
      header: "Логин",
      render: (user) => (
        <span className="font-mono text-xs">
          {user.login}
          {user.id === me.id ? <span className="ml-1.5 text-fg-muted">(вы)</span> : null}
        </span>
      ),
    },
    { key: "display_name", header: "Имя", render: (user) => user.display_name },
    { key: "role", header: "Роль", render: (user) => <StatusBadge status={user.role} map={ROLE_META} /> },
    { key: "status", header: "Статус", render: (user) => <UserStatus user={user} now={now} /> },
    {
      key: "tg",
      header: "Telegram",
      render: (user) => <span className="font-mono text-xs text-fg-muted">{user.tg_user_id ?? "—"}</span>,
    },
    {
      key: "last_login",
      header: "Последний вход",
      render: (user) =>
        user.last_login_at ? (
          <span title={formatDateTime(user.last_login_at)} className="text-fg-muted">
            {formatAgo(user.last_login_at, now)}
          </span>
        ) : (
          <span className="text-fg-muted">не входил</span>
        ),
    },
    {
      key: "actions",
      header: <span className="sr-only">Действия</span>,
      className: "w-px text-right",
      render: (user) => (
        <div className="flex justify-end gap-1">
          <Button variant="ghost" size="xs" onClick={() => setEditing(user)} aria-label={`Изменить ${user.login}`}>
            <Pencil className="h-3.5 w-3.5" aria-hidden="true" />
            Изменить
          </Button>
          <Button
            variant="ghost"
            size="xs"
            onClick={() => setResetting(user)}
            aria-label={`Сбросить пароль ${user.login}`}
          >
            <KeyRound className="h-3.5 w-3.5" aria-hidden="true" />
            Пароль
          </Button>
        </div>
      ),
    },
  ];

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-base font-semibold">Пользователи и роли</h2>
          <p className="text-xs text-fg-muted">
            Владелец — всё; оператор — контент и каталог; наблюдатель — только чтение. Вход в Telegram-бот по
            tg_user_id.
          </p>
        </div>
        <Button onClick={() => setCreating(true)}>
          <UserPlus className="h-4 w-4" aria-hidden="true" />
          Добавить
        </Button>
      </div>

      {users.isError ? (
        <SectionError error={users.error} onRetry={() => void users.refetch()} />
      ) : (
        <DataTable
          caption="Пользователи"
          columns={columns}
          rows={users.data ?? []}
          rowKey={(user) => String(user.id)}
          isLoading={users.isPending}
          emptyTitle="Пользователей нет"
          emptyDescription="Первого владельца создаёт `autoshorts-admin create-user`."
          minWidth={760}
        />
      )}

      <CreateUserDialog open={creating} onClose={() => setCreating(false)} />
      {editing ? (
        <EditUserDialog user={editing} isSelf={editing.id === me.id} onClose={() => setEditing(null)} />
      ) : null}
      {resetting ? <ResetPasswordDialog user={resetting} onClose={() => setResetting(null)} /> : null}
    </div>
  );
}

function FormRootError({ message }: { message?: string }) {
  if (!message) return null;
  return (
    <p role="alert" className="rounded-md border border-negative/40 bg-negative/10 px-2 py-1.5 text-xs text-negative">
      {message}
    </p>
  );
}

function CreateUserDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const create = useCreateUser();
  const form = useForm<CreateValues>({
    resolver: zodResolver(createSchema),
    defaultValues: {
      login: "",
      display_name: "",
      role: "operator",
      password: "",
      password_confirm: "",
      tg_user_id: "",
    },
  });
  const { errors, isSubmitting } = form.formState;
  const role = useWatch({ control: form.control, name: "role" });

  const close = () => {
    form.reset();
    onClose();
  };

  const onSubmit = form.handleSubmit(async (values) => {
    try {
      await create.mutateAsync({
        login: values.login,
        display_name: values.display_name,
        role: values.role,
        password: values.password,
        tg_user_id: tgToNumber(values.tg_user_id),
      });
      toast.success(`Пользователь ${values.login} создан`);
      close();
    } catch (error) {
      if (error instanceof ConflictError) {
        form.setError("login", { type: "server", message: error.message || "Такой логин уже есть" });
        return;
      }
      applyServerErrors(error, form.setError, ["login", "display_name", "role", "password", "tg_user_id"]);
    }
  });

  return (
    <Dialog
      open={open}
      onClose={close}
      title="Новый пользователь"
      description="Пароль не короче 12 символов. Сменить его пользователь сможет сам."
    >
      <form onSubmit={onSubmit} className="flex flex-col gap-3" noValidate>
        <div className="grid gap-3 sm:grid-cols-2">
          <FormField label="Логин" htmlFor="new-login" error={errors.login?.message}>
            <Input
              id="new-login"
              autoFocus
              autoComplete="off"
              spellCheck={false}
              aria-invalid={!!errors.login}
              {...form.register("login")}
            />
          </FormField>
          <FormField label="Имя" htmlFor="new-name" error={errors.display_name?.message}>
            <Input id="new-name" aria-invalid={!!errors.display_name} {...form.register("display_name")} />
          </FormField>
        </div>
        <FormField
          label="Роль"
          htmlFor="new-role"
          error={errors.role?.message}
          hint={ROLE_OPTIONS.find((option) => option.value === role)?.hint}
        >
          <Select id="new-role" {...form.register("role")}>
            {ROLE_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </Select>
        </FormField>
        <div className="grid gap-3 sm:grid-cols-2">
          <FormField label="Пароль" htmlFor="new-password" error={errors.password?.message}>
            <Input
              id="new-password"
              type="password"
              autoComplete="new-password"
              aria-invalid={!!errors.password}
              {...form.register("password")}
            />
          </FormField>
          <FormField label="Пароль ещё раз" htmlFor="new-password-confirm" error={errors.password_confirm?.message}>
            <Input
              id="new-password-confirm"
              type="password"
              autoComplete="new-password"
              aria-invalid={!!errors.password_confirm}
              {...form.register("password_confirm")}
            />
          </FormField>
        </div>
        <FormField
          label="Telegram user id (необязательно)"
          htmlFor="new-tg"
          error={errors.tg_user_id?.message}
          hint="Для приёмки в боте"
        >
          <Input
            id="new-tg"
            inputMode="numeric"
            className="font-mono"
            aria-invalid={!!errors.tg_user_id}
            {...form.register("tg_user_id")}
          />
        </FormField>
        <FormRootError message={errors.root?.server?.message} />
        <div className="flex justify-end gap-2 pt-1">
          <Button variant="outline" onClick={close}>
            Отмена
          </Button>
          <Button type="submit" disabled={isSubmitting}>
            {isSubmitting ? "Создаю…" : "Создать"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

function EditUserDialog({ user, isSelf, onClose }: { user: User; isSelf: boolean; onClose: () => void }) {
  const update = useUpdateUser();
  const form = useForm<EditValues>({
    resolver: zodResolver(editSchema),
    defaultValues: {
      display_name: user.display_name,
      role: user.role,
      is_active: user.is_active,
      tg_user_id: user.tg_user_id === null ? "" : String(user.tg_user_id),
    },
  });
  const { errors, isSubmitting } = form.formState;

  const onSubmit = form.handleSubmit(async (values) => {
    try {
      await update.mutateAsync({
        userId: user.id,
        body: {
          display_name: values.display_name,
          // Себе роль и активность не меняем: так владелец не запрёт сам себя.
          ...(isSelf ? {} : { role: values.role, is_active: values.is_active }),
          tg_user_id: tgToNumber(values.tg_user_id),
        },
      });
      toast.success(`Изменения для ${user.login} сохранены`);
      onClose();
    } catch (error) {
      applyServerErrors(error, form.setError, ["display_name", "role", "is_active", "tg_user_id"]);
    }
  });

  return (
    <Dialog
      open
      onClose={onClose}
      title={`Пользователь ${user.login}`}
      description={isSelf ? "Свою роль и активность изменить нельзя." : undefined}
    >
      <form onSubmit={onSubmit} className="flex flex-col gap-3" noValidate>
        <FormField label="Имя" htmlFor="edit-name" error={errors.display_name?.message}>
          <Input id="edit-name" autoFocus aria-invalid={!!errors.display_name} {...form.register("display_name")} />
        </FormField>
        <FormField label="Роль" htmlFor="edit-role" error={errors.role?.message}>
          <Select id="edit-role" disabled={isSelf} {...form.register("role")}>
            {ROLE_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </Select>
        </FormField>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            className="h-4 w-4 accent-[var(--accent)]"
            disabled={isSelf}
            {...form.register("is_active")}
          />
          Активен (может входить)
        </label>
        <FormField label="Telegram user id" htmlFor="edit-tg" error={errors.tg_user_id?.message}>
          <Input
            id="edit-tg"
            inputMode="numeric"
            className="font-mono"
            aria-invalid={!!errors.tg_user_id}
            {...form.register("tg_user_id")}
          />
        </FormField>
        <FormRootError message={errors.root?.server?.message} />
        <div className="flex justify-end gap-2 pt-1">
          <Button variant="outline" onClick={onClose}>
            Отмена
          </Button>
          <Button type="submit" disabled={isSubmitting}>
            {isSubmitting ? "Сохраняю…" : "Сохранить"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

function ResetPasswordDialog({ user, onClose }: { user: User; onClose: () => void }) {
  const reset = useResetPassword();
  const form = useForm<ResetValues>({
    resolver: zodResolver(resetSchema),
    defaultValues: { new_password: "", confirm: "" },
  });
  const { errors, isSubmitting } = form.formState;

  const onSubmit = form.handleSubmit(async (values) => {
    try {
      await reset.mutateAsync({ userId: user.id, newPassword: values.new_password });
      toast.success(`Пароль ${user.login} сброшен`, { description: "Его активные сессии завершены сервером." });
      onClose();
    } catch (error) {
      if (error instanceof ApiError && error.status === 422) {
        applyServerErrors(error, form.setError, ["new_password"]);
        return;
      }
      form.setError("root.server", { type: "server", message: errorMessage(error) });
    }
  });

  return (
    <Dialog
      open
      onClose={onClose}
      title={`Новый пароль для ${user.login}`}
      description="Не короче 12 символов. Передайте его пользователю лично."
    >
      <form onSubmit={onSubmit} className="flex flex-col gap-3" noValidate>
        <FormField label="Новый пароль" htmlFor="reset-password" error={errors.new_password?.message}>
          <Input
            id="reset-password"
            autoFocus
            type="password"
            autoComplete="new-password"
            aria-invalid={!!errors.new_password}
            {...form.register("new_password")}
          />
        </FormField>
        <FormField label="Ещё раз" htmlFor="reset-confirm" error={errors.confirm?.message}>
          <Input
            id="reset-confirm"
            type="password"
            autoComplete="new-password"
            aria-invalid={!!errors.confirm}
            {...form.register("confirm")}
          />
        </FormField>
        <FormRootError message={errors.root?.server?.message} />
        <div className="flex justify-end gap-2 pt-1">
          <Button variant="outline" onClick={onClose}>
            Отмена
          </Button>
          <Button type="submit" variant="destructive" disabled={isSubmitting}>
            {isSubmitting ? "Сбрасываю…" : "Сбросить пароль"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
