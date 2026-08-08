import { useMemo, useRef, useSyncExternalStore } from "react";

/**
 * A value that changes many times a second and must not re-render its writer.
 *
 * Built for the stream charts' cursor. uPlot owns a canvas: creating one is
 * expensive and destroying one mid-gesture throws away the zoom and the drag
 * the athlete is in the middle of. If the cursor position were ordinary
 * component state, every mousemove would re-render the panels that publish
 * it — and any prop of theirs derived inline would change identity, re-run
 * their create-effect, and rebuild the charts sixty times a second.
 *
 * So the value lives outside React and only the components that *read* it
 * subscribe. `set` is stable for the lifetime of the store, which is what
 * lets it be passed to a panel as a prop without invalidating anything.
 */
export interface LiveValue<T> {
  /** Register a listener; returns the unsubscribe. */
  readonly subscribe: (listener: () => void) => () => void;
  /** The current value. Stable between writes, as `useSyncExternalStore` needs. */
  readonly get: () => T;
  /** Publish a new value. Stable across renders; a no-op when unchanged. */
  readonly set: (value: T) => void;
}

/** Create a live value that survives re-renders of the component holding it. */
export function useLiveValue<T>(initial: T): LiveValue<T> {
  const state = useRef<{ value: T; listeners: Set<() => void> }>(null);
  state.current ??= { value: initial, listeners: new Set() };
  const held = state.current;

  return useMemo(
    () => ({
      subscribe: (listener: () => void) => {
        held.listeners.add(listener);
        return () => {
          held.listeners.delete(listener);
        };
      },
      get: () => held.value,
      set: (value: T) => {
        if (Object.is(held.value, value)) {
          return;
        }
        held.value = value;
        for (const listener of held.listeners) {
          listener();
        }
      },
    }),
    [held],
  );
}

/** Subscribe to a live value. Only the calling component re-renders. */
export function useLive<T>(live: LiveValue<T>): T {
  return useSyncExternalStore(live.subscribe, live.get, live.get);
}
