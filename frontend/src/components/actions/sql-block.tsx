// Agent-provided SQL is rendered as plain text, never as HTML.
export function SQLBlock({ sql }: { sql: string | null }) {
  if (sql === null) return <p className="text-sm text-muted-foreground">No SQL recorded</p>;
  return (
    // tabIndex: a scrollable area must be reachable by keyboard so its overflow can be read.
    <pre
      tabIndex={0}
      aria-label="SQL"
      className="max-h-96 overflow-auto rounded-md bg-muted p-3 font-mono text-xs break-words whitespace-pre-wrap"
    >
      <code>{sql}</code>
    </pre>
  );
}
