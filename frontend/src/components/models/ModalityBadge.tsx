import { Badge } from "@/components/ui/badge";

export type ModalityDirection = "in" | "out";

interface ModalityBadgeProps {
  modality: string;
  /** Whether the model READS this or PRODUCES it. */
  direction: ModalityDirection;
}

const labelMap: Record<string, string> = {
  text: "Text",
  image: "Image",
  audio: "Audio",
  video: "Video",
};

const verbMap: Record<ModalityDirection, string> = {
  in: "reads",
  out: "makes",
};

/**
 * One capability of a model, and which WAY it goes.
 *
 * The direction used to be invisible. An "Image" badge was rendered identically
 * whether the model could look at a picture or draw one, which are entirely
 * different things to a reader choosing a model - and the two were emitted from
 * adjacent loops over input_modalities and output_modalities, so a
 * vision-and-drawing model showed "Image" twice with no way to tell why.
 *
 * The arrow carries the distinction at a glance, the colour reinforces it
 * (producing something is the rarer, more notable capability, so it takes the
 * accent), and the title spells it out for anyone who wants the full sentence.
 */
export function ModalityBadge({ modality, direction }: ModalityBadgeProps) {
  const label = labelMap[modality] ?? modality;
  const out = direction === "out";
  return (
    <Badge
      variant="outline"
      // 11px, not 10: these were the smallest text in the panel and the first
      // thing anyone asked about.
      className="gap-0.5 text-[11px] font-normal leading-none"
      title={`This model ${verbMap[direction]} ${label.toLowerCase()}`}
      style={{
        borderColor: out
          ? "rgba(62, 114, 176, 0.35)"
          : "var(--color-es-glass-border-dark)",
        color: out
          ? "var(--color-es-primary-sage)"
          : "var(--color-es-text-muted)",
        backgroundColor: out ? "rgba(62, 114, 176, 0.10)" : "transparent",
      }}
    >
      <span aria-hidden="true" style={{ opacity: 0.75 }}>
        {out ? "↑" : "↓"}
      </span>
      {label}
    </Badge>
  );
}
