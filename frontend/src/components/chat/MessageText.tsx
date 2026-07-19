/**
 * MessageText - message body text with roleplay inline styling.
 *
 * Renders *asterisk narration* (italic, muted ink) and "quoted speech"
 * (amber tint, marks kept visible) as pure React spans - no HTML strings.
 * Both stylings are user preferences (Settings → Narration style); with
 * both off this renders the raw string byte-for-byte, zero parser cost.
 *
 * Streaming callers pass `streaming` so a trailing asterisk run is withheld
 * while it may still be growing; unclosed spans style to the end in BOTH
 * modes, so the stream→settle transition never restyles (the invariant the
 * variant carousel protects). The caller keeps its cursor as a sibling.
 */
import { Fragment, useMemo } from "react";
import { parseMessage } from "@/lib/chat/parseMessage";
import { useUiStore } from "@/lib/store/uiStore";

interface MessageTextProps {
  text: string;
  streaming?: boolean;
}

export function MessageText({ text, streaming = false }: MessageTextProps) {
  const narrationEnabled = useUiStore((s) => s.narrationEnabled);
  const quoteTintEnabled = useUiStore((s) => s.quoteTintEnabled);

  const segments = useMemo(() => {
    if (!narrationEnabled && !quoteTintEnabled) return null;
    return parseMessage(text, {
      streaming,
      emphasis: narrationEnabled,
      quotes: quoteTintEnabled,
    });
  }, [text, streaming, narrationEnabled, quoteTintEnabled]);

  if (segments == null) return <>{text}</>;

  return (
    <>
      {segments.map((seg, index) => {
        const className =
          [
            seg.em ? "narration-span" : "",
            seg.strong ? "strong-span" : "",
            seg.quote ? "quote-span" : "",
          ]
            .filter(Boolean)
            .join(" ") || null;
        return className ? (
          <span key={index} className={className}>
            {seg.text}
          </span>
        ) : (
          <Fragment key={index}>{seg.text}</Fragment>
        );
      })}
    </>
  );
}
