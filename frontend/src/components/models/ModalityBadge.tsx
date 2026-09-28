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
      className="gap-0.5 text-micro font-normal leading-none"
      title={`This model ${verbMap[direction]} ${label.toLowerCase()}`}
      style={{
        borderColor: out
          ? "rgba(62, 114, 176, 0.35)"
          : "var(--color-es-glass-border-dark)",
        // The accent's deep step, not the accent: #3E72B0 on this badge's own
        // tint reads 3.74:1 at the panel's bottom stop, under the 4.5:1 its
        // words need (KURAL 2.4). The deep step reads 5.93:1 there, and it is
        // the colour the source badge's border already uses.
        color: out
          ? "var(--color-es-primary-sage-deep)"
          : "var(--color-es-text-muted)",
        backgroundColor: out ? "rgba(62, 114, 176, 0.10)" : "transparent",
      }}
    >
      {/* At the badge's full ink. Dimmed to 0.75 the arrow read 2.93:1 with
          the opacity applied (KURAL 2.4b), and it is the one mark on the
          badge that says which way the capability goes. */}
      <span aria-hidden="true">
        {out ? "↑" : "↓"}
      </span>
      {label}
    </Badge>
  );
}
