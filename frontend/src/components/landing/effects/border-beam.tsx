// Adapted from Magic UI's Border Beam, in CSS only (`.landing-beam` in globals.css): a short light
// travels the parent's border. Off under prefers-reduced-motion. The parent must be `relative`.
export function BorderBeam() {
  return <span aria-hidden="true" className="landing-beam" />;
}
