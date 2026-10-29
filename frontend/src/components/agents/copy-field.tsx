"use client";

import { Check, Copy } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";

// Plain text the user can also select by hand; Copy writes exactly this string.
export function CopyField({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);

  async function copy() {
    try {
      // Needs a secure context (localhost or https); elsewhere clipboard is undefined and this throws.
      await navigator.clipboard.writeText(value);
    } catch {
      toast.error("Copy failed — select the text and copy it");
      return;
    }
    setCopied(true);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setCopied(false), 2000);
  }

  return (
    <div className="space-y-1">
      <p className="text-sm font-medium">{label}</p>
      <div className="flex items-start gap-2">
        <code className="min-w-0 flex-1 rounded-md bg-muted px-2 py-1.5 font-mono text-xs [overflow-wrap:anywhere]">
          {value}
        </code>
        <Button
          type="button"
          variant="outline"
          size="sm"
          aria-label={`Copy ${label}`}
          onClick={() => void copy()}
        >
          {copied ? <Check /> : <Copy />}
          {copied ? "Copied" : "Copy"}
        </Button>
      </div>
    </div>
  );
}
