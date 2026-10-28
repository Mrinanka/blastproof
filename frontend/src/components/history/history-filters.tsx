"use client";

import { ChevronDown } from "lucide-react";
import { useState } from "react";

import { STATUS_META } from "@/components/actions/status-badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Field, FieldDescription, FieldLabel, FieldTitle } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import type { Schemas } from "@/lib/api/client";
import {
  STATUS_ORDER,
  hasFilters,
  isDay,
  toggleStatus,
  type FilterPatch,
  type HistoryFilters as Filters,
} from "@/lib/history-filters";

type AgentView = Schemas["AgentView"];

interface Props {
  filters: Filters;
  // Changes whenever the URL does: drafts in the text and date boxes follow it (back, forward).
  urlKey: string;
  onChange: (patch: FilterPatch) => void;
  onClear: () => void;
  agents: AgentView[] | undefined;
  agentsPending: boolean;
  agentsFailed: boolean;
  isAdmin: boolean;
}

// A box's typed text, reset to the URL's value whenever the URL changes.
function useDraft(value: string, urlKey: string): [string, (v: string) => void] {
  const [draft, setDraft] = useState(value);
  const [seen, setSeen] = useState(urlKey);
  if (seen !== urlKey) {
    setSeen(urlKey);
    setDraft(value);
  }
  return [seen === urlKey ? draft : value, setDraft];
}

function agentLabel(agent: AgentView, isAdmin: boolean): string {
  const notes = [
    isAdmin ? agent.owner.display_name : null,
    agent.is_active ? null : "inactive",
  ].filter((n): n is string => n !== null);
  return notes.length > 0 ? `${agent.name} (${notes.join(", ")})` : agent.name;
}

function statusLabel(filters: Filters): string {
  if (filters.statuses.length === 0) return "All";
  if (filters.statuses.length === 1) return STATUS_META[filters.statuses[0]].label;
  return `${filters.statuses.length} selected`;
}

export function HistoryFilters({
  filters,
  urlKey,
  onChange,
  onClear,
  agents,
  agentsPending,
  agentsFailed,
  isAdmin,
}: Props) {
  const [table, setTable] = useDraft(filters.table ?? "", urlKey);
  const [from, setFrom] = useDraft(filters.from ?? "", urlKey);
  const [to, setTo] = useDraft(filters.to ?? "", urlKey);
  const knownAgent = agents?.some((a) => a.id === filters.agentId) ?? false;

  function commitTable() {
    const value = table.trim() || null;
    if (value !== filters.table) onChange({ table: value });
  }

  // A date is sent once it is complete (or emptied); a half-typed year stays a draft.
  function changeDay(key: "from" | "to", value: string, setDraft: (v: string) => void) {
    setDraft(value);
    const next = value === "" ? null : isDay(value) ? value : undefined;
    if (next !== undefined && next !== filters[key]) onChange({ [key]: next });
  }

  return (
    <div className="flex flex-wrap items-end gap-3">
      <Field className="w-auto">
        <FieldTitle>Status</FieldTitle>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="outline"
              className="justify-between gap-2"
              aria-label={`Status: ${statusLabel(filters)}`}
            >
              {statusLabel(filters)}
              <ChevronDown aria-hidden="true" className="size-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start">
            {STATUS_ORDER.map((s) => (
              <DropdownMenuCheckboxItem
                key={s}
                checked={filters.statuses.includes(s)}
                // Keep the menu open so several statuses can be picked in a row.
                onSelect={(event) => event.preventDefault()}
                onCheckedChange={(on) =>
                  onChange({ statuses: toggleStatus(filters.statuses, s, on === true) })
                }
              >
                {STATUS_META[s].label}
              </DropdownMenuCheckboxItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      </Field>

      <Field className="w-auto">
        <FieldLabel htmlFor="history-agent">Agent</FieldLabel>
        <NativeSelect
          id="history-agent"
          // Every control is the default height (32 px), so the labels line up.
          // Capped: an admin's labels carry owner names and would stretch the bar.
          className="w-56 max-w-full"
          value={filters.agentId ?? ""}
          disabled={agentsFailed}
          onChange={(event) => onChange({ agentId: event.target.value || null })}
        >
          <NativeSelectOption value="">All agents</NativeSelectOption>
          {filters.agentId && !knownAgent ? (
            <NativeSelectOption value={filters.agentId}>
              {agentsPending ? "Loading…" : "Unknown agent"}
            </NativeSelectOption>
          ) : null}
          {(agents ?? []).map((a) => (
            <NativeSelectOption key={a.id} value={a.id}>
              {agentLabel(a, isAdmin)}
            </NativeSelectOption>
          ))}
        </NativeSelect>
        {agentsFailed ? <FieldDescription>Could not load agents.</FieldDescription> : null}
      </Field>

      <Field className="w-auto">
        <FieldLabel htmlFor="history-table">Table</FieldLabel>
        <Input
          id="history-table"
          className="w-40"
          placeholder="e.g. users"
          maxLength={63}
          value={table}
          onChange={(event) => setTable(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              commitTable();
            }
          }}
          onBlur={commitTable}
        />
      </Field>

      <Field className="w-auto">
        <FieldLabel htmlFor="history-from">From</FieldLabel>
        <Input
          id="history-from"
          type="date"
          className="w-40"
          max={filters.to ?? undefined}
          value={from}
          onChange={(event) => changeDay("from", event.target.value, setFrom)}
        />
      </Field>

      <Field className="w-auto">
        <FieldLabel htmlFor="history-to">To</FieldLabel>
        <Input
          id="history-to"
          type="date"
          className="w-40"
          min={filters.from ?? undefined}
          value={to}
          onChange={(event) => changeDay("to", event.target.value, setTo)}
        />
      </Field>

      <Button variant="ghost" disabled={!hasFilters(filters)} onClick={onClear}>
        Clear filters
      </Button>
    </div>
  );
}
