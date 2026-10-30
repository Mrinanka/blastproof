"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useRef, useState } from "react";
import { useForm } from "react-hook-form";

import { errorText } from "@/components/common/states";
import { Button } from "@/components/ui/button";
import { Field, FieldError, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { useCreateAgent } from "@/hooks/use-agents";
import { agentSchema, type AgentValues, type CreatedAgent } from "@/lib/agents";

export function CreateAgentForm({ onCreated }: { onCreated: (agent: CreatedAgent) => void }) {
  const create = useCreateAgent();
  const [formError, setFormError] = useState<string | null>(null);
  // isPending lags a click by a render, so a quick second submit would create a second agent.
  const inflight = useRef(false);
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors },
  } = useForm<AgentValues>({ resolver: zodResolver(agentSchema), defaultValues: { name: "" } });

  function onSubmit({ name }: AgentValues) {
    if (inflight.current) return;
    inflight.current = true;
    setFormError(null);
    create.mutate(name, {
      onSuccess: (agent) => {
        reset();
        onCreated(agent);
        // The mutation would otherwise keep the key in its result; the page holds it from here.
        create.reset();
      },
      onError: (e) => setFormError(errorText(e)),
      onSettled: () => {
        inflight.current = false;
      },
    });
  }

  return (
    <form
      onSubmit={(e) => void handleSubmit(onSubmit)(e)}
      noValidate
      className="max-w-xl space-y-4"
    >
      <FieldGroup>
        <Field data-invalid={!!errors.name || !!formError}>
          <FieldLabel htmlFor="agent-name">Agent name</FieldLabel>
          <Input
            id="agent-name"
            autoComplete="off"
            aria-invalid={!!errors.name || !!formError}
            {...register("name")}
          />
          <FieldError errors={[errors.name]} />
        </Field>
        {formError ? <FieldError>{formError}</FieldError> : null}
      </FieldGroup>
      <Button type="submit" disabled={create.isPending}>
        {create.isPending ? "Creating…" : "Create agent"}
      </Button>
    </form>
  );
}
