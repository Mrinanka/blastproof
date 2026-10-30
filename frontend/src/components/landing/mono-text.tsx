// Renders `backticked` words from content.ts in monospace (product names, SQL keywords). Plain
// text only: the content is ours, but it is never parsed as HTML.
export function MonoText({ text }: { text: string }) {
  return (
    <>
      {text.split("`").map((part, i) =>
        i % 2 === 1 ? (
          <code key={i} className="font-mono text-[0.92em] text-foreground">
            {part}
          </code>
        ) : (
          part
        ),
      )}
    </>
  );
}

// The same text without the backticks, for alt text, metadata and tests.
export function plain(text: string): string {
  return text.replaceAll("`", "");
}
