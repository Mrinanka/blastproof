"use client";

import { useRef, useState } from "react";
import { toast } from "sonner";

import { errorText } from "@/components/common/states";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { useUploadKnowledge } from "@/hooks/use-knowledge";
import { ApiError } from "@/lib/api/errors";
import { checkUploadFile, replaces, type KnowledgeSource } from "@/lib/knowledge";

// The backend keeps going after the dashboard gives up, so a 504 is not a failure to retry blindly.
function uploadError(error: unknown): string {
  if (error instanceof ApiError && error.status === 504) {
    return "The upload may still be finishing; refresh the list in a minute.";
  }
  return errorText(error);
}

export function UploadForm({ sources, tables }: { sources: KnowledgeSource[]; tables: string[] }) {
  const upload = useUploadKnowledge();
  const input = useRef<HTMLInputElement>(null);
  // isPending lags a click by a render, so a quick second submit would upload twice.
  const inflight = useRef(false);
  const [file, setFile] = useState<File | null>(null);
  const [tags, setTags] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const existing = file ? replaces(sources, file.name) : undefined;

  function toggle(table: string, on: boolean) {
    setTags((current) => (on ? [...current, table] : current.filter((t) => t !== table)));
  }

  function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (inflight.current) return;
    const problem = checkUploadFile(file);
    setError(problem);
    if (problem || !file) return;
    inflight.current = true;
    // Sent in the order the schema lists them, whatever order they were ticked in.
    upload.mutate(
      { file, tables: tables.filter((t) => tags.includes(t)) },
      {
        onSettled: () => {
          inflight.current = false;
        },
        onSuccess: () => {
          toast.success(`Uploaded ${file.name}`);
          setFile(null);
          setTags([]);
          if (input.current) input.current.value = "";
        },
        // The chosen file stays, so the admin can retry without picking it again.
        onError: (e) => setError(uploadError(e)),
      },
    );
  }

  return (
    <form onSubmit={onSubmit} noValidate className="max-w-xl space-y-4">
      <FieldGroup>
        <Field data-invalid={!!error}>
          <FieldLabel htmlFor="knowledge-file">Document (.md or .txt, up to 1 MB)</FieldLabel>
          <Input
            id="knowledge-file"
            ref={input}
            type="file"
            accept=".md,.txt"
            aria-invalid={!!error}
            onChange={(e) => {
              setFile(e.target.files?.[0] ?? null);
              setError(null);
            }}
          />
          {existing ? (
            <FieldDescription>
              This replaces the existing {existing.source} ({existing.chunks} chunks).
            </FieldDescription>
          ) : null}
        </Field>
        <fieldset className="space-y-2">
          <legend className="text-sm font-medium">Tag tables (optional)</legend>
          {tables.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              Rescan the schema to tag tables. An untagged upload still works.
            </p>
          ) : (
            <>
              <p className="text-sm text-muted-foreground">
                The AI reads a tagged document whenever a change touches one of these tables.
              </p>
              <div className="flex flex-wrap gap-x-5 gap-y-2">
                {tables.map((table) => (
                  <label key={table} className="flex items-center gap-2 text-sm">
                    <Checkbox
                      checked={tags.includes(table)}
                      onCheckedChange={(on) => toggle(table, on === true)}
                    />
                    {table}
                  </label>
                ))}
              </div>
            </>
          )}
        </fieldset>
        {error ? <FieldError>{error}</FieldError> : null}
      </FieldGroup>
      <Button type="submit" disabled={upload.isPending}>
        {upload.isPending ? "Uploading…" : "Upload"}
      </Button>
      {upload.isPending ? (
        <p className="text-sm text-muted-foreground">Large documents can take a minute to index.</p>
      ) : null}
    </form>
  );
}
