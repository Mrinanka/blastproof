// True when any part of the element is inside the viewport. The reveal and the number ticker only
// rewind what the visitor cannot see yet, so the server HTML stays the final state (D-292).
export function isInViewport(el: Element): boolean {
  const rect = el.getBoundingClientRect();
  return rect.top < window.innerHeight && rect.bottom > 0;
}

export const EASE_OUT: [number, number, number, number] = [0.16, 1, 0.3, 1];
