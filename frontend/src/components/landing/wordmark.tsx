import Image from "next/image";

import icon from "@/app/icon.png";
import { cn } from "@/lib/utils";

export function Wordmark({ className }: { className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-2 font-semibold tracking-tight", className)}>
      <Image src={icon} alt="" width={22} height={22} className="size-[22px]" />
      Blastproof
    </span>
  );
}
