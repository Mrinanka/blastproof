"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { errorText } from "@/components/common/states";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Field, FieldError, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Textarea } from "@/components/ui/textarea";

// Trimmed first, like the backend's ReviewerReason (strip, then 10 to 500 characters).
export const rejectSchema = z.object({
  reason: z
    .string()
    .trim()
    .min(10, "Give a reason of at least 10 characters.")
    .max(500, "Keep the reason to 500 characters or fewer."),
});

type RejectValues = z.infer<typeof rejectSchema>;

export function RejectDialog({
  onReject,
  disabled,
}: {
  onReject: (reason: string) => Promise<unknown>;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<RejectValues>({ resolver: zodResolver(rejectSchema) });

  function onOpenChange(next: boolean) {
    setOpen(next);
    if (!next) {
      reset();
      setFormError(null);
    }
  }

  async function onSubmit({ reason }: RejectValues) {
    setFormError(null);
    try {
      await onReject(reason);
      onOpenChange(false);
    } catch (error) {
      // The dialog stays open so the reviewer's text is not lost.
      setFormError(errorText(error));
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogTrigger asChild>
        <Button variant="outline" disabled={disabled}>
          Reject…
        </Button>
      </DialogTrigger>
      <DialogContent>
        <form onSubmit={handleSubmit(onSubmit)} noValidate className="grid gap-4">
          <DialogHeader>
            <DialogTitle>Reject this change</DialogTitle>
            <DialogDescription>
              The reason is sent back to the agent so it can adjust.
            </DialogDescription>
          </DialogHeader>
          <FieldGroup>
            <Field data-invalid={!!errors.reason}>
              <FieldLabel htmlFor="reject-reason">Reason</FieldLabel>
              <Textarea
                id="reject-reason"
                rows={4}
                aria-invalid={!!errors.reason}
                {...register("reason")}
              />
              <FieldError errors={[errors.reason]} />
            </Field>
            {formError && <FieldError>{formError}</FieldError>}
          </FieldGroup>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" variant="destructive" disabled={isSubmitting}>
              {isSubmitting ? "Rejecting…" : "Reject"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
