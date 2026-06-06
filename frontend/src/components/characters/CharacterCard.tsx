import type { Character } from "@/lib/schemas/characters";
import { useUiStore } from "@/lib/store/uiStore";

interface CharacterCardProps {
  character: Character;
}

export function CharacterCard({ character }: CharacterCardProps) {
  const selectedId = useUiStore((s) => s.selectedCharacterId);
  const selectCharacter = useUiStore((s) => s.selectCharacter);
  const isSelected = selectedId === character.id;
  const initial = character.name.trim().charAt(0) || "?";

  return (
    <button
      type="button"
      onClick={() => selectCharacter(character.id)}
      className={`sidebar-item w-full rounded-xl px-3 py-2 text-left ${
        isSelected ? "sidebar-item-selected" : "sidebar-item-unselected"
      }`}
      aria-label={`Select character ${character.name}`}
      aria-pressed={isSelected}
    >
      <div className="flex items-center gap-2.5">
        <span
          className="char-avatar"
          style={{
            backgroundColor: isSelected
              ? "rgba(167, 200, 161, 0.20)"
              : "rgba(255, 255, 255, 0.06)",
            color: isSelected
              ? "var(--color-es-primary-sage)"
              : "var(--color-es-text-muted)",
          }}
          aria-hidden="true"
        >
          {initial}
        </span>
        <div className="min-w-0 flex-1">
          <p
            className="truncate text-sm font-medium"
            style={{ color: "var(--color-es-text-light)" }}
          >
            {character.name}
          </p>
          {character.description && (
            <p
              className="truncate text-[11px]"
              style={{ color: "var(--color-es-text-muted)" }}
            >
              {character.description}
            </p>
          )}
        </div>
      </div>
    </button>
  );
}
