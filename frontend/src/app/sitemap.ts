import type { MetadataRoute } from "next";

import { SITE_URL } from "@/lib/landing/site";

// Only the public landing page; /login and /dashboard are not for search engines (FE-29).
export default function sitemap(): MetadataRoute.Sitemap {
  return [{ url: `${SITE_URL}/`, changeFrequency: "monthly", priority: 1 }];
}
