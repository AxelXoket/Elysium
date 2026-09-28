/**
 * focusHandoff - one-shot focus requests for controls that replace others.
 */
/** A one-shot focus request: `want(key)` arms it, and the element whose
 *  callback ref is `take(key)` takes focus when it mounts (or on the next
 *  render it is there), once. For a control that REPLACES another one
 *  (a confirm pane cross-fading in), where no element exists yet to focus
 *  at the moment of the press. `holder` bridges the gap. */
export function focusHandoff() {
  let wanted: string | null = null;
  return {
    /** Arm `key`. `holder`, if given, takes focus at once and keeps it
     *  until the new control mounts: the pressed control is about to leave,
     *  and without a holder the keyboard would sit on the page's body for
     *  the length of the cross-fade. */
    want(key: string, holder?: HTMLElement | null) {
      wanted = key;
      holder?.focus({ preventScroll: true });
    },
    take(key: string) {
      return (el: HTMLElement | null) => {
        if (el && wanted === key) {
          wanted = null;
          el.focus({ preventScroll: true });
        }
      };
    },
  };
}
