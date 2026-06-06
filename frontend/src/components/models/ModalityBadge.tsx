import { Badge } from "@/components/ui/badge";

interface ModalityBadgeProps {
  modality: string;
}

const labelMap: Record<string, string> = {
  text: "Text",
  image: "Image",
  audio: "Audio",
  video: "Video",
};

export function ModalityBadge({ modality }: ModalityBadgeProps) {
  return (
    <Badge
      variant="outline"
      className="text-[10px] font-normal"
      style={{
        borderColor: "var(--color-es-border-dark)",
        color: "var(--color-es-text-muted)",
      }}
    >
      {labelMap[modality] ?? modality}
    </Badge>
  );
}
