import { useCharacters } from "@/lib/query/characters";
import { CharacterCard } from "@/components/characters/CharacterCard";
import { CharacterCreateDialog } from "@/components/characters/CharacterCreateDialog";
import { CharacterImportDialog } from "@/components/characters/CharacterImportDialog";
import { AnimatedList, AnimatedListItem } from "@/components/motion/AnimatedList";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { Plus, FileJson } from "lucide-react";
import { isApiError } from "@/lib/api/client";

export function CharacterList() {
  const { data: characters, isLoading, error } = useCharacters();

  return (
    <div className="h-full overflow-y-auto px-3 py-3">
      {/* Section header */}
      <div className="flex items-center justify-between px-1 mb-2">
        <p
          className="text-[11px] font-semibold uppercase tracking-widest"
          style={{ color: "var(--color-es-text-muted)", opacity: 0.75 }}
        >
          Characters
        </p>
        <div className="flex gap-0.5">
          <CharacterCreateDialog
            trigger={
              <Button
                variant="ghost"
                size="sm"
                className="h-6 w-6 p-0 opacity-60 hover:opacity-100"
                aria-label="Create character"
              >
                <Plus size={12} />
              </Button>
            }
          />
          <CharacterImportDialog
            trigger={
              <Button
                variant="ghost"
                size="sm"
                className="h-6 w-6 p-0 opacity-60 hover:opacity-100"
                aria-label="Import character"
              >
                <FileJson size={12} />
              </Button>
            }
          />
        </div>
      </div>

      {/* Loading */}
      {isLoading && (
        <div className="space-y-1">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton
              key={i}
              className="h-11 w-full rounded-xl"
              style={{ backgroundColor: "rgba(255,255,255,0.04)" }}
            />
          ))}
        </div>
      )}

      {/* Error */}
      {error && (
        <p
          className="mt-2 rounded-xl px-3 py-2 text-xs"
          style={{
            backgroundColor: "rgba(201, 110, 91, 0.08)",
            color: "var(--color-es-danger)",
          }}
        >
          {isApiError(error) ? error.detail : "Failed to load characters"}
        </p>
      )}

      {/* List */}
      {characters && characters.length > 0 && (
        <AnimatedList className="space-y-0.5">
          {characters.map((char) => (
            <AnimatedListItem key={char.id}>
              <CharacterCard character={char} />
            </AnimatedListItem>
          ))}
        </AnimatedList>
      )}

      {/* Empty */}
      {characters && characters.length === 0 && (
        <div
          className="mt-2 rounded-xl p-4 text-center text-xs"
          style={{
            backgroundColor: "rgba(255,255,255,0.03)",
            color: "var(--color-es-text-muted)",
          }}
        >
          No characters yet
        </div>
      )}
    </div>
  );
}
