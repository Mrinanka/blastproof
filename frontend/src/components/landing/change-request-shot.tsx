import Image from "next/image";

import { BorderBeam } from "./effects/border-beam";
import { Section } from "./section";
import { ShotTilt } from "./shot-tilt";

// A real screenshot of the dashboard's change request page: the Replit-style delete run through the
// real workflow on the local demo database (1440 x 1000 at 2x, dark theme, F13).
export const SHOT = {
  src: "/change-request.png",
  width: 2880,
  height: 2000,
  alt: "Blastproof's change request page for the Replit-style delete, awaiting review. The headline reads: Deletes 48,213 rows from users (96.4% of the table), including 12 in users.paying. Below it are the per-table counts, Approve and Reject buttons, and the agent's original SQL next to the pinned SQL as it will run.",
};

export function ChangeRequestShot() {
  return (
    <Section
      id="change-request"
      eyebrow="The change request"
      title="The owner sees the outcome, then decides."
      intro="Each held write gets one page: what will happen in plain language, the numbers behind it, the exact SQL, the rules that fired and an audit trail."
    >
      <figure>
        <ShotTilt>
          <div className="landing-edge relative rounded-xl bg-card p-1.5 sm:p-2">
            <Image
              src={SHOT.src}
              width={SHOT.width}
              height={SHOT.height}
              alt={SHOT.alt}
              sizes="(min-width: 1152px) 1104px, calc(100vw - 2rem)"
              className="h-auto w-full rounded-lg [mask-image:linear-gradient(to_bottom,#000_82%,transparent)]"
            />
            <BorderBeam />
          </div>
        </ShotTilt>
        <figcaption className="mt-5 text-center text-sm text-muted-foreground">
          What the owner sees: the outcome, the affected rows and the exact SQL.
        </figcaption>
      </figure>
    </Section>
  );
}
