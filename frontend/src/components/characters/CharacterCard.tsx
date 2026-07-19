import type { Character } from "@/lib/schemas/characters";
import { useUiStore } from "@/lib/store/uiStore";
import { CharacterEditDialog } from "./CharacterEditDialog";
import { Pencil } from "lucide-react";

interface CharacterCardProps {
  character: Character;
}

export function CharacterCard({ character }: CharacterCardProps) {
  const selectedId = useUiStore((s) => s.selectedCharacterId);
  const selectCharacter = useUiStore((s) => s.selectCharacter);
  const isSelected = selectedId === character.id;
  const initial = character.name.trim().charAt(0) || "?";

  return (
    // Same hover-action pattern as ChatListItem: the wrapper reveals the
    // edit trigger on hover/focus-within via the chat-list-item CSS rules.
    <div
      className={`chat-list-item sidebar-item rounded-xl ${
        isSelected ? "sidebar-item-selected" : "sidebar-item-unselected"
      }`}
    >
      <button
        type="button"
        onClick={() => selectCharacter(character.id)}
        className="chat-list-select"
        aria-label={`Select character ${character.name}`}
        aria-pressed={isSelected}
      >
        <div className="flex items-center gap-2.5">
          <span
            className="char-avatar"
            style={{
              backgroundColor: isSelected
                ? "rgba(62, 114, 176, 0.20)"
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

      {/* Edit trigger is a sibling of the select button - opening the editor
          never changes the selected character. */}
      <CharacterEditDialog
        character={character}
        trigger={
          <button
            type="button"
            className="chat-action-trigger"
            aria-label="Edit character"
          >
            <Pencil size={13} />
          </button>
        }
      />
    </div>
  );
}
