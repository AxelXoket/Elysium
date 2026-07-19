/**
 * chatBgDb.ts - Blob storage for the user's chat background image.
 *
 * The ONLY approved IndexedDB call-site (static-safety S-13 lists this file
 * explicitly). Scope is a single decorative preference blob: never
 * conversation content, drafts, or secrets. The Blob pipeline exists so no
 * base64/data: URI is ever constructed (S-21) and localStorage is never
 * asked to serialize megabytes on every store write.
 *
 * All requests are promise-wrapped; environments without IndexedDB (jsdom)
 * resolve to null/no-op instead of throwing.
 */

const DB_NAME = "elysium-appearance";
const DB_VERSION = 1;
const STORE = "chat-bg";
const KEY = "current";

function hasDb(): boolean {
  return typeof indexedDB !== "undefined";
}

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE)) {
        db.createObjectStore(STORE);
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function withStore<T>(
  mode: IDBTransactionMode,
  run: (store: IDBObjectStore) => IDBRequest<T>,
): Promise<T> {
  return openDb().then(
    (db) =>
      new Promise<T>((resolve, reject) => {
        const tx = db.transaction(STORE, mode);
        const req = run(tx.objectStore(STORE));
        tx.oncomplete = () => {
          db.close();
          resolve(req.result);
        };
        tx.onerror = () => {
          db.close();
          reject(tx.error);
        };
        tx.onabort = () => {
          db.close();
          reject(tx.error);
        };
      }),
  );
}

/** Store (replace) the background image blob. */
export function putChatBgBlob(blob: Blob): Promise<void> {
  if (!hasDb()) return Promise.resolve();
  return withStore("readwrite", (store) => store.put(blob, KEY)).then(
    () => undefined,
  );
}

/** Read the stored background image blob, or null when none exists. */
export function getChatBgBlob(): Promise<Blob | null> {
  if (!hasDb()) return Promise.resolve(null);
  return withStore<Blob | undefined>("readonly", (store) =>
    store.get(KEY),
  ).then((value) => (value instanceof Blob ? value : null));
}

/** Remove the stored background image blob. */
export function deleteChatBgBlob(): Promise<void> {
  if (!hasDb()) return Promise.resolve();
  return withStore("readwrite", (store) => store.delete(KEY)).then(
    () => undefined,
  );
}
