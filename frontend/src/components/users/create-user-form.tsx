"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useRef, useState } from "react";
import { useForm } from "react-hook-form";

import { errorText } from "@/components/common/states";
import { Button } from "@/components/ui/button";
import { Field, FieldError, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { useCreateUser } from "@/hooks/use-users";
import { ApiError } from "@/lib/api/errors";
import { userSchema, type UserValues } from "@/lib/users";

const EMPTY: UserValues = { email: "", display_name: "", password: "", role: "member" };

export function CreateUserForm() {
  const create = useCreateUser();
  const [emailError, setEmailError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  // isPending lags a click by a render, so a quick second submit would send a second POST.
  const inflight = useRef(false);
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors },
  } = useForm<UserValues>({ resolver: zodResolver(userSchema), defaultValues: EMPTY });

  function onSubmit(values: UserValues) {
    if (inflight.current) return;
    inflight.current = true;
    setEmailError(null);
    setFormError(null);
    create.mutate(values, {
      // Cleared on success only, so a 409 can be fixed without retyping the password.
      onSuccess: () => reset(EMPTY),
      onError: (e) => {
        if (e instanceof ApiError && e.status === 409) setEmailError(e.message);
        else setFormError(errorText(e));
      },
      onSettled: () => {
        inflight.current = false;
      },
    });
  }

  const emailBad = !!errors.email || !!emailError;
  return (
    <form
      onSubmit={(e) => void handleSubmit(onSubmit)(e)}
      noValidate
      className="max-w-xl space-y-4"
    >
      <FieldGroup>
        <Field data-invalid={emailBad}>
          <FieldLabel htmlFor="user-email">Email</FieldLabel>
          <Input
            id="user-email"
            type="email"
            autoComplete="off"
            aria-invalid={emailBad}
            {...register("email")}
          />
          <FieldError errors={[errors.email]} />
          {emailError ? <FieldError>{emailError}</FieldError> : null}
        </Field>
        <Field data-invalid={!!errors.display_name}>
          <FieldLabel htmlFor="user-display-name">Display name</FieldLabel>
          <Input
            id="user-display-name"
            autoComplete="off"
            aria-invalid={!!errors.display_name}
            {...register("display_name")}
          />
          <FieldError errors={[errors.display_name]} />
        </Field>
        <Field data-invalid={!!errors.password}>
          <FieldLabel htmlFor="user-password">Initial password</FieldLabel>
          <Input
            id="user-password"
            type="password"
            autoComplete="new-password"
            aria-invalid={!!errors.password}
            {...register("password")}
          />
          <FieldError errors={[errors.password]} />
        </Field>
        <Field>
          <FieldLabel htmlFor="user-role">Role</FieldLabel>
          <NativeSelect id="user-role" {...register("role")}>
            <NativeSelectOption value="member">Member</NativeSelectOption>
            <NativeSelectOption value="admin">Admin</NativeSelectOption>
          </NativeSelect>
        </Field>
        {formError ? <FieldError>{formError}</FieldError> : null}
      </FieldGroup>
      <Button type="submit" disabled={create.isPending}>
        {create.isPending ? "Creating…" : "Create user"}
      </Button>
    </form>
  );
}
