import { useCharacters } from "@/lib/query/characters";
import { CharacterCard } from "@/components/characters/CharacterCard";
import { CharacterCreateDialog } from "@/components/characters/CharacterCreateDialog";
import { CharacterImportDialog } from "@/components/characters/CharacterImportDialog";
import { AnimatedList, AnimatedListItem } from "@/components/motion/AnimatedList";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { Plus, FileJson } from "lucide-react";
import { parseApiError } from "@/lib/errors";

interface CharacterListProps {
  /** Client-side name filter from the SEARCH section (empty = show all). */
  query?: string;
}

export function CharacterList({ query = "" }: CharacterListProps) {
  const { data: characters, isLoading, error } = useCharacters();

  const q = query.trim().toLowerCase();
  const filtered =
    characters && q
      ? characters.filter((c) => c.name.toLowerCase().includes(q))
      : characters;

  return (
    <div className="flex h-full flex-col overflow-hidden px-3 py-3">
      {/* Section header - actions moved to the pinned footer below */}
      <p
        className="mb-2 px-1 text-[11px] font-semibold uppercase tracking-widest"
        style={{ color: "var(--color-es-text-muted)", opacity: 0.75 }}
      >
        Characters
      </p>

      <div className="min-h-0 flex-1 overflow-y-auto">
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
            className="mt-1 rounded-xl px-3 py-2 text-xs"
            style={{
              backgroundColor: "rgba(195, 106, 114, 0.08)",
              color: "var(--color-es-danger)",
            }}
          >
            {parseApiError(error).message}
          </p>
        )}

        {/* List */}
        {filtered && filtered.length > 0 && (
          <AnimatedList className="space-y-0.5">
            {filtered.map((char) => (
              <AnimatedListItem key={char.id}>
                <CharacterCard character={char} />
              </AnimatedListItem>
            ))}
          </AnimatedList>
        )}

        {/* Empty - distinguishes "no matches" from "no characters at all" */}
        {characters && characters.length > 0 && filtered?.length === 0 && (
          <div
            className="mt-1 rounded-xl p-4 text-center text-xs"
            style={{
              backgroundColor: "rgba(255,255,255,0.03)",
              color: "var(--color-es-text-muted)",
            }}
          >
            No characters match "{query.trim()}"
          </div>
        )}
        {characters && characters.length === 0 && (
          <div
            className="mt-1 rounded-xl p-4 text-center text-xs"
            style={{
              backgroundColor: "rgba(255,255,255,0.03)",
              color: "var(--color-es-text-muted)",
            }}
          >
            No characters yet
          </div>
        )}
      </div>

      {/* Pinned actions: primary New Character + secondary import */}
      <div className="mt-2 flex items-center gap-1.5">
        <CharacterCreateDialog
          trigger={
            <Button
              type="button"
              className="sidebar-primary-action h-9 flex-1 gap-2 rounded-lg text-xs"
              style={{ color: "var(--color-es-text-light)" }}
            >
              <Plus size={14} />
              New Character
            </Button>
          }
        />
        <CharacterImportDialog
          trigger={
            <Button
              type="button"
              variant="ghost"
              className="sidebar-secondary-action h-9 w-9 shrink-0 p-0"
              aria-label="Import character from JSON"
              title="Import character from JSON"
            >
              <FileJson size={14} />
            </Button>
          }
        />
      </div>
    </div>
  );
}
