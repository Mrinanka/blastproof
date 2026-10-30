// F13 guards: FE-29 (page metadata, Open Graph image, sitemap, robots).
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { alt, size } from "../src/app/opengraph-image";
import { metadata } from "../src/app/page";
import robots from "../src/app/robots";
import sitemap from "../src/app/sitemap";
import { PITCH, SITE_URL } from "../src/lib/landing/site";

describe("sitemap and robots", () => {
  it("the sitemap lists only the landing page", () => {
    expect(sitemap().map((e) => e.url)).toEqual(["https://blastproof.in/"]);
  });

  it("robots allows the landing page, hides the app and points at the sitemap", () => {
    const r = robots();
    expect(r.sitemap).toBe("https://blastproof.in/sitemap.xml");
    expect(r.rules).toMatchObject({ userAgent: "*", allow: "/" });
    const disallow = (r.rules as { disallow: string[] }).disallow;
    expect(disallow).toEqual(expect.arrayContaining(["/dashboard", "/api"]));
  });
});

describe("landing page metadata", () => {
  it("has a title, the pitch, Open Graph and a large Twitter card", () => {
    expect(metadata.title).toEqual({ absolute: "Blastproof: approve outcomes, not commands" });
    expect(metadata.description).toBe(PITCH);
    expect(metadata.openGraph).toMatchObject({
      type: "website",
      url: "/",
      title: expect.any(String),
    });
    expect(metadata.twitter).toMatchObject({ card: "summary_large_image" });
  });

  it("the root layout resolves URLs against the site URL", () => {
    expect(SITE_URL).toBe("https://blastproof.in");
    const layout = readFileSync(resolve(__dirname, "../src/app/layout.tsx"), "utf8");
    expect(layout).toContain("metadataBase: new URL(SITE_URL)");
  });

  it("the Open Graph image is 1200 x 630 with alt text", () => {
    expect(size).toEqual({ width: 1200, height: 630 });
    expect(alt).toContain("48,213");
  });

  it("the Twitter image reuses the Open Graph image", () => {
    const twitter = readFileSync(resolve(__dirname, "../src/app/twitter-image.tsx"), "utf8");
    expect(twitter).toContain('from "./opengraph-image"');
  });
});
