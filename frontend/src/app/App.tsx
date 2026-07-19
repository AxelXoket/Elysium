import { AppShell } from "@/components/layout/AppShell";
import { VaultGate } from "@/components/vault/VaultGate";

/**
 * App.tsx - root composition / layout (no routing in Phase 6A).
 * VaultGate holds the shell back until the encrypted DB is unlocked.
 */
export function App() {
  return (
    <VaultGate>
      <AppShell />
    </VaultGate>
  );
}
