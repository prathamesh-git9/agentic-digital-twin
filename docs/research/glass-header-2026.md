# Glass header research, 2026

## Verdict

1. Keep `.bar` completely transparent and non-painting, and keep all material on the existing centered `.bar-island`; the detached silhouette is the single biggest fix for the “white strip” problem.
2. Reduce the island’s scrolled light fill from the current 74% near-white to about 58%, and use about 32% at scroll-top, so colour can pass through without leaving body copy legible.
3. Keep one real backdrop filter on the small island, at `blur(20px) saturate(175%)` in light mode and `blur(20px) saturate(160%) brightness(92%)` in dark mode, with explicit `-webkit-backdrop-filter` twins.
4. Build the edge as a ring—semi-transparent border, a directional 1px inset top highlight, a faint inner half-pixel ring, and a restrained outer shadow—because this communicates thickness better than a uniform white outline.
5. On scroll, change only fill, rim, and shadow; do not animate width, height, radius, or the blur filter.
6. Retain one static top-to-bottom specular sheen on `.bar-island::before`; do not animate it, and do not add animated grain.
7. Do not ship a five-to-twelve-layer progressive-blur stack or SVG displacement refraction on this page: both are disproportionate to the repository’s measured rendering budget.
8. Provide opaque `prefers-reduced-transparency`, `prefers-contrast`, forced-colours, and unsupported-browser fallbacks rather than assuming translucency is always usable.

### Scope, method, and repository reality

Research and live-source inspection were performed on 29 August 2026. “Measured” below means read from the HTML, CSS, or JavaScript served by the public site on that date. Hashed asset URLs can change on the next deployment. The live pages and served assets were fetchable, but an interactive browser runtime was unavailable, so visual-only runtime behaviour is labelled **unverified** rather than inferred.

The prompt’s full-bleed `.bar` excerpt is not the final cascade in the working tree. The current stylesheet makes `.bar` a transparent positioning shell (`styles.css:271`, `styles.css:3043-3052`), puts the material on a content-hugging `.bar-island` (`styles.css:272`, `styles.css:1595-1618`), and gives that island a 22px/190% filter (`styles.css:1498`, `styles.css:2471-2474`). The contract at `tests/test_chat_interface_contract.py:302-349` rejects any background or active backdrop filter on the full-width shell. The copy-paste specification at the end therefore refines `.bar-island`; painting `.bar` would both restore the band and violate the repository contract.

## 1. Cluely.com specifically

### What the live header actually is

The current public landing-page header is **not glass and not a detached pill**. It is a transparent, full-width, absolutely positioned shell over the blue hero, with its contents constrained to 72rem. Because it is `position: absolute`, the main header scrolls away with the document. This comes directly from the [live Cluely HTML](https://cluely.com), the [served Tailwind CSS](https://cluely.com/_next/static/css/e0f47fde64bf82ef.css), and the [served header JavaScript](https://cluely.com/_next/static/chunks/app/(website)/layout-1c5ff03756cb6a41.js).

| Property | Measured live value | Evidence |
|---|---:|---|
| Positioning | `position:absolute; width:100%; z-index:9999` | Header class `absolute z-9999 flex w-full` in the [live HTML](https://cluely.com). |
| Top inset | `top: var(--header-v2-top, 0px)` plus `padding-top: 10px` | Inline style and `pt-2.5`; `--spacing:.25rem` in the [served CSS](https://cluely.com/_next/static/css/e0f47fde64bf82ef.css). |
| Content width | `width:100%; max-width:72rem` = **1152px** | `max-w-6xl`; `--container-6xl:72rem` in the [served CSS](https://cluely.com/_next/static/css/e0f47fde64bf82ef.css). |
| Header shape | No visible material shape; content frame has only 6px bottom corners | `rounded-b-lg` resolves to `--radius:.375rem`; the frame has no fill or border in the [served CSS](https://cluely.com/_next/static/css/e0f47fde64bf82ef.css). |
| Left cluster radius | 16px, but visually irrelevant because the cluster has no fill | `rounded-2xl`; `--radius-2xl:1rem` in the [served CSS](https://cluely.com/_next/static/css/e0f47fde64bf82ef.css). |
| Padding | Header top 10px; inner right 20px / 32px at `md`; left cluster 12px inline and 4px block; links 14px inline and 8px block | `pt-2.5`, `pr-5 md:pr-8`, `px-3 py-1`, and `px-3.5 py-2` in the [live HTML](https://cluely.com), resolved through the [served CSS](https://cluely.com/_next/static/css/e0f47fde64bf82ef.css). |
| Background | `transparent` / none | No background utility or header-specific background declaration in the [live HTML](https://cluely.com) or [served CSS](https://cluely.com/_next/static/css/e0f47fde64bf82ef.css). |
| Backdrop filter | `none` | No backdrop-filter utility on the header tree in the [live HTML](https://cluely.com). |
| Border/ring | `none` | No border utility on the header or content frame in the [live HTML](https://cluely.com). |
| Shadow | `none` | No shadow utility on the header or content frame in the [live HTML](https://cluely.com). |
| Header scroll change | None; it does not shrink, gain opacity, gain a border, or animate width | The header is always the same absolute element in the [served JavaScript](https://cluely.com/_next/static/chunks/app/(website)/layout-1c5ff03756cb6a41.js). |

There is a separate fixed desktop CTA at `right:10px` and `top:calc(var(--header-v2-top, 0px) + 10px)`. Its opacity transitions over 300ms from 0 to 1 when the hero `#download-button` is no longer fully inside the viewport; this is the only header-adjacent scroll reaction in the [served JavaScript](https://cluely.com/_next/static/chunks/app/(website)/layout-1c5ff03756cb6a41.js). It does not turn the main header into glass.

**Plain conclusion:** copying current Cluely exactly would mean removing the header material entirely and letting white navigation sit directly on a controlled blue hero. It would not produce an Apple-style glass header. If the owner remembers a glass treatment, it is from an older deployment, a product UI, or a secondary reproduction—not the public landing page served on the research date.

### Design discussion and clones

- A1 Gallery attributes the 2025 site to Alex Barashkov, tags it as animated/skeuomorphic, and records its Next.js build and Forma/Inter fonts; it does not publish header CSS measurements ([A1 Gallery](https://www.a1.gallery/website/cluely)).
- The designer described the redesign as roughly 1,200 hours and three months of work, but did not publish the header implementation in the post ([Alex Barashkov on LinkedIn](https://www.linkedin.com/posts/barashkov-alex_meet-the-new-look-of-cluely-1200-hours-activity-7359676873506770947-sryV)). The corresponding [X post](https://x.com/alex_barashkov/status/1953905339012657222) returned no readable body to the fetcher, so thread details are **unverified**.
- SaaSFrame’s archived prose calls the navigation “sticky,” which conflicts with the current served `position:absolute`; treat that description as outdated or generic rather than implementation evidence ([SaaSFrame](https://www.saasframe.io/examples/cluely-landing-page)).
- The public repositories named as Cluely clones reproduce the desktop assistant/product behaviour, not the current marketing header. They are not evidence for landing-page measurements ([CluelyClone](https://github.com/1300Sarthak/CluelyClone), [Cass](https://github.com/jadenpxrk/Cass)).
- A 2025 recording preserves the overall landing-page experience but does not expose computed styles ([Lapa Ninja](https://www.lapa.ninja/video/post/cluely/)). No public clone with a source-verifiable, pixel-matched version of the current header was found; any precise clone numbers beyond the served source above are **unverified**.

## 2. Apple Liquid Glass: bars in iOS 26 and macOS 26

### What Apple specifies

Apple treats Liquid Glass as a distinct **functional layer for controls and navigation**, floating above content—not as a translucent background applied to every surface. Standard navigation bars, tab bars, and toolbars adopt it automatically, and Apple explicitly advises removing custom bar backgrounds that can interfere with the material or scroll-edge effect ([Adopting Liquid Glass](https://developer.apple.com/documentation/TechnologyOverviews/adopting-liquid-glass?changes=latest_major%2Clatest_major), [WWDC25 “Get to know the new design system”](https://developer.apple.com/videos/play/wwdc2025/356/)).

For bars, the relevant behaviour is the **scroll edge effect**:

- Apple defines it as a variable blur transition between scrolling content and an area containing Liquid Glass controls ([HIG: Scroll views](https://developer.apple.com/design/human-interface-guidelines/scroll-views?changes=_7)).
- At scroll-top, there is no need for a strong separating band because content has not moved under the controls. As content begins to pass underneath, the soft effect dissolves that content into the background and visually lifts the controls ([WWDC25 “Meet Liquid Glass”](https://developer.apple.com/videos/play/wwdc2025/219/)).
- **Soft** is the default, particularly on iOS and iPadOS. **Hard** is more opaque and has a defined boundary; Apple mainly uses it on macOS or where unbacked interactive text, controls, or pinned table headers need stronger separation ([WWDC25 “Get to know the new design system”](https://developer.apple.com/videos/play/wwdc2025/356/)).
- Apple says to apply one scroll-edge effect per view and not stack or mix soft and hard effects. It is functional, not decorative ([WWDC25 “Get to know the new design system”](https://developer.apple.com/videos/play/wwdc2025/356/)).
- Custom bars can register the overlaid controls that shape the effect through `safeAreaBar(...)` or `UIScrollEdgeElementContainerInteraction`; descendants such as labels, images, glass views, and controls participate in the edge shape ([Adopting Liquid Glass](https://developer.apple.com/documentation/TechnologyOverviews/adopting-liquid-glass?changes=latest_major%2Clatest_major), [`UIScrollEdgeElementContainerInteraction`](https://developer.apple.com/documentation/UIKit/UIScrollEdgeElementContainerInteraction)).

The material itself is more than frost:

- **Lensing:** Apple describes transparent objects through warped and bent light; Liquid Glass bends and concentrates the content underneath, rather than merely scattering it with a blur ([WWDC25 “Meet Liquid Glass”](https://developer.apple.com/videos/play/wwdc2025/219/)).
- **Specular edge:** highlights respond to geometry, interaction, and in some cases device motion; shadow opacity increases over text and decreases over quiet light ground ([WWDC25 “Meet Liquid Glass”](https://developer.apple.com/videos/play/wwdc2025/219/), [Platforms State of the Union](https://developer.apple.com/videos/play/wwdc2025/102/?id=707)).
- **Adaptive tint and dynamic contrast:** tint, shadow, and dynamic range continuously react to what is behind the control. Small nav and tab bars can switch between light and dark material and flip their glyphs; large surfaces adapt but avoid whole-surface light/dark flipping because it would be distracting ([WWDC25 “Meet Liquid Glass”](https://developer.apple.com/videos/play/wwdc2025/219/)).
- **Size-aware thickness:** larger glass becomes more opaque, casts deeper shadows, and uses stronger lensing/refraction and softer scattering; smaller glass is clearer and can switch appearance more aggressively ([WWDC25 UIKit session](https://developer.apple.com/videos/play/wwdc2025/284/?time=1309)).
- **Concentric corners:** nested controls align with their containing window/device curvature. Apple provides `ConcentricRectangle`, `cornerConfiguration`, and container-relative corner configuration rather than publishing one universal radius ([Adopting Liquid Glass](https://developer.apple.com/documentation/TechnologyOverviews/adopting-liquid-glass?changes=latest_major%2Clatest_major), [WWDC25 UIKit session](https://developer.apple.com/videos/play/wwdc2025/284/?time=1309)).
- **Materialization, not alpha fading:** native glass is animated by changing the effect; Apple explicitly prefers setting the effect over animating alpha, and glass elements in a shared container can merge like droplets ([WWDC25 UIKit session](https://developer.apple.com/videos/play/wwdc2025/284/?time=1353)).

Apple does **not** publish CSS-equivalent blur radii, rgba fills, rings, or box-shadow numbers. Any web recipe claiming to be the exact Apple material is an approximation.

### What pure CSS can and cannot reproduce

| Apple behaviour | Pure CSS in current browsers? | Practical web analogue |
|---|---|---|
| Translucent tint, blur, saturation | Yes | Semi-transparent fill plus paired `backdrop-filter` declarations. |
| Floating navigation geometry | Yes | Detached capsule/island with clear air around every edge. |
| Static specular edge and depth shadow | Yes | Directional inset 1px highlight, subtle inner ring, low outer shadow. |
| Scroll-top vs scrolled material state | Yes | Existing `.solid` class changes fill/rim/shadow; avoid animating filter geometry. |
| Concentric radii | Manually | Choose radii from container inset; CSS has no Apple container-relative optical-radius engine. |
| Soft scroll edge | Approximate | One masked, constant-blur pseudo-element, or a costly multi-layer blur stack. |
| Per-pixel tint/dynamic-range adaptation | No portable CSS equivalent | Theme-level light/dark tokens; JavaScript sampling would still be much coarser. |
| True lensing/refraction of arbitrary backdrop | Not cross-browser | Chromium-only SVG-filter experiments; Safari has open gaps. |
| Motion-aware highlights and materialization | No portable CSS equivalent | Restrained static sheen; transform/opacity interaction feedback only. |
| Automatic droplet merging | No | Requires native APIs or a custom shader/canvas/WebGL system. |

The correct target for this repository is therefore “Apple-informed CSS glass,” not a claim of native Liquid Glass parity.

## 3. Progressive / gradient blur

### Canonical layered implementations

CSS `blur()` is spatially uniform. The canonical workaround stacks multiple full-area backdrop-filter layers and gives each a different alpha mask. Kenneth Nym’s implementation uses seven layers ([“Progressive blur in CSS”](https://kennethnym.com/blog/progressive-blur-in-css/)):

| Layer | Blur | Mask stops, top to bottom |
|---:|---:|---|
| 1 | 1px | transparent at 0; black 10%-30%; transparent 40% |
| 2 | 2px | transparent 10%; black 20%-40%; transparent 50% |
| 3 | 4px | transparent 15%; black 30%-50%; transparent 60% |
| 4 | 8px | transparent 20%; black 40%-60%; transparent 70% |
| 5 | 16px | transparent 40%; black 60%-80%; transparent 90% |
| 6 | 32px | transparent 60%; black 80%-100% |
| 7 | intended 64px | transparent 70%; black 100% |

The published seventh rule says `background-filter: blur(64px)`, which is a typo; there is no standard `background-filter` property. It would need `backdrop-filter` (and the WebKit twin) to participate ([source CSS](https://kennethnym.com/blog/progressive-blur-in-css/)).

Preet Suthar’s 2026 version reduces the stack to five layers ([“Progressive blur”](https://preetsuthar.me/writing/progressive-blur)):

```css
.layer[data-blur="1"]  { backdrop-filter: blur(1px);  mask-image: linear-gradient(to top, transparent 0%,  black 50%); }
.layer[data-blur="2"]  { backdrop-filter: blur(2px);  mask-image: linear-gradient(to top, transparent 15%, black 60%); }
.layer[data-blur="4"]  { backdrop-filter: blur(4px);  mask-image: linear-gradient(to top, transparent 30%, black 72%); }
.layer[data-blur="8"]  { backdrop-filter: blur(8px);  mask-image: linear-gradient(to top, transparent 50%, black 85%); }
.layer[data-blur="16"] { backdrop-filter: blur(16px); mask-image: linear-gradient(to top, transparent 70%, black 100%); }
```

Each production rule also needs `-webkit-backdrop-filter` and `-webkit-mask-image`. The weakest blur must actually fade to zero at the free edge; otherwise the stack still ends in a visible seam ([Preet Suthar](https://preetsuthar.me/writing/progressive-blur)).

A two-pseudo-element compromise uses 0.2rem and 1.5rem blurs with carefully shaped opacity stops. It is smaller than a five-layer stack but still consumes two backdrop passes; the exact masks are published in the [Kaori Igawa CodePen](https://codepen.io/igawa_kaori/pen/jEEvZyZ).

### A real 2026 production stack: Clerk

Clerk currently renders **12** full-area masked layers behind its header: blur radii `1, 1, 2, 2, 3, 4, 5, 6, 8, 10, 12, 16px`. Every mask is opaque from 40% and reaches transparent at, respectively, `100, 95, 90, 85, 80, 75, 70, 65, 60, 55, 50, 45%`. The stack is 3.15rem (50.4px) high and starts 0.5rem above the header. Its `--mask-opacity` is mapped from scrollY `0..300` to `0..1`, so the edge effect fades in progressively rather than switching at one threshold ([live Clerk HTML](https://clerk.com), [served Clerk header JavaScript](https://clerk.com/_next/static/chunks/23gc8m4ngif4t.js?dpl=dpl_DiBd3WWCMdGL3JjeDM5GREGUwTZT)). This is impressive and expensive; it is a reference, not a suitable transplant for this repository.

### The newer single-element approach

One pseudo-element can fade a **constant** blur to transparent:

```css
.soft-edge::after {
  content: "";
  position: absolute;
  inset: 100% 0 auto;
  height: 48px;
  -webkit-backdrop-filter: blur(16px);
  backdrop-filter: blur(16px);
  -webkit-mask-image: linear-gradient(to bottom, #000, transparent);
  mask-image: linear-gradient(to bottom, #000, transparent);
  pointer-events: none;
}
```

This removes a hard visible cutoff, but it is not a true spatially varying blur kernel: it blends between an unblurred result and one uniformly blurred result. WebKit’s standards-position issue makes that distinction explicit, and the proposed `linear-blur()`/two-radius syntax is not a web standard or shipping browser feature as of the research date ([WebKit standards position request](https://github.com/WebKit/standards-positions/issues/595), [CSSWG proposal archive](https://lists.w3.org/Archives/Public/public-css-archive/2026Jan/0001.html)).

Resend uses a production variant after 10px of scroll: an `::after` with `rgba(0,0,0,.60)`, a texture, and 12px blur, plus a second pseudo-element extending 120px below the header with 40px blur, 200% brightness, and a one-pixel-tall mask position that creates a soft trailing transition ([live Resend HTML](https://resend.com), [served Resend JavaScript](https://resend.com/_next/static/immutable/chunks/1tndkv8p849xi.js), [served Resend utility CSS](https://resend.com/_next/static/immutable/chunks/1k04l1c7k8c6v.css)). It is visually relevant but far beyond this repository’s intended cost.

### Browser support and Safari/iOS caveats

- `backdrop-filter` is Baseline 2024 and `mask-image` is Baseline December 2023 in current browser documentation ([MDN backdrop-filter](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/backdrop-filter), [MDN mask-image](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/mask-image), [Can I Use](https://caniuse.com/backdrop-filter)).
- In ordinary modern Safari/iOS rendering, one masked backdrop-filter element is usable, and production sites such as Clerk and Resend ship both prefixed and unprefixed forms. It is not “set and forget”: an ancestor mask, opacity, filter, clip-path, or backdrop-filter creates a new backdrop root and can change what a descendant samples ([MDN backdrop-filter](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/backdrop-filter)).
- WebKit’s old incorrect clipping of a masked backdrop was fixed in 2017 ([WebKit bug 167456](https://bugs.webkit.org/show_bug.cgi?id=167456)), but current edge cases remain: masked images can disappear in print output ([WebKit bug 281686](https://bugs.webkit.org/show_bug.cgi?id=281686)), and backdrop filters can disappear during view transitions ([WebKit bug 302256](https://bugs.webkit.org/show_bug.cgi?id=302256)).
- Chromium can misrender stacked backdrop filters inside an `overflow:hidden` ancestor; applying the radius to each layer instead of clipping a common wrapper is the published workaround ([Preet Suthar](https://preetsuthar.me/writing/progressive-blur)).

For this header, ordinary blur with air around a detached island gives most of the desired soft-edge perception for one pass. A masked second pass is technically viable but not recommended under the measured budget.

## 4. How current product sites implement headers

The table uses served values, not screenshots. “None found” means no header-specific state was found in served HTML/CSS/JS; where interactive execution was needed, runtime behaviour is marked **unverified**.

| Site | Pattern and geometry | Fill | Blur | Border/ring and shadow | Scroll behaviour | Sources |
|---|---|---|---:|---|---|---|
| Linear | Fixed full-bleed bar; 72px desktop / 64px mobile; inner max width `1344px + outer padding` | Transparent at top; scrolled light `#fffc` (80% white), scrolled dark `#08090a` (opaque) | 20px | 1px bottom line at 8% black/white; no radius or outer shadow | `data-scrolled` changes fill and line; no shrink or width animation | [Live](https://linear.app), [header CSS](https://static.linear.app/web/_next/static/css/Header.C7eR1ZyF.css), [tokens](https://static.linear.app/web/_next/static/css/index.jjy8s6kJ.css) |
| Vercel | Sticky full-bleed bar, 64px | Transparent on the homepage at top; scrolled `#fafafa` light / `#000` dark, both opaque | None | No border radius; `0 1px 0` line using 8% black light / 14% white dark | Gains opaque fill and hairline when `data-scrolled` is present | [Live](https://vercel.com), [served CSS](https://vercel.com/vc-ap-vercel-marketing/_next/static/immutable/chunks/25wxk4ma0-fkq.css) |
| Raycast | Fixed detached island; 16px top and side inset; max 1204px; 58px mobile / 76px desktop; 16px radius; 16px padding | Gradient `#111214bf` (75%) to `#0c0d0fe6` (90%) | 5px | 1px `#ffffff0f` (~6%) border; inset `0 1px 1px #ffffff26` (~15%); no outer shadow | No scroll state found; height/transform transition is used for menu opening. Runtime scroll is **unverified** | [Live](https://www.raycast.com), [header CSS](https://www.raycast.com/_next/static/immutable/chunks/2frcgw3_vp50k.css), [tokens](https://www.raycast.com/_next/static/immutable/chunks/2a4dsuyz5iclh.css) |
| Arc | Fixed full-bleed themed bar; content max 1280px plus 32px page padding; 96px desktop height | Opaque `rgba(49,57,251,1)` on the inspected homepage, plus `/noise-light.png` | None | No header radius, border, or shadow | No scroll state found; runtime change is **unverified** | [Live](https://arc.net), [served CSS](https://arc.net/_next/static/css/df28c1bc1b1a6c7d.css) |
| Stripe | Static, normal-flow transparent navigation; 76px; constrained content layout | Transparent | None on base header | None on base header | No base scroll change. Opening the menu adds an overlay gradient ending at `rgba(236,239,241,.8)` with 5px blur | [Live](https://stripe.com), [served CSS](https://b.stripecdn.com/mkt-ssr-statics/assets/_next/static/css/b024dbf1f58fc3c9.css) |
| Framer | Fixed full-width 64px frame; inner max 1200px; 20px inline padding | `rgba(0,0,0,0)` in served desktop nav | None in the served base nav | None in the served base nav | A runtime-only state could not be executed; scroll behaviour is **unverified** | [Live/source HTML](https://framer.com) |
| Resend | Sticky full-bleed bar; inner max 1280px | Transparent at top; after 10px, `rgba(0,0,0,.60)` plus texture | 12px on fill layer, plus a 40px masked trailing layer | No base radius, border, or shadow | At `scrollY > 10`, both pseudo-elements transition to opacity 1 over 200ms | [Live](https://resend.com), [header JS](https://resend.com/_next/static/immutable/chunks/1tndkv8p849xi.js), [utility CSS](https://resend.com/_next/static/immutable/chunks/1k04l1c7k8c6v.css) |
| Clerk | Sticky detached island at top 8px; max 76.75rem = 1228px; viewport inset 8px mobile / 16px desktop; 12px radius | `rgba(248,248,248,.9)` light / `rgba(19,19,22,.90)` dark | Base pill has none; separate 12-layer edge stack runs from 1px to 16px | 0.5px inset white ring, 0.5px outer dark ring, then 1–6px low-alpha shadows | Blur-stack mask opacity maps scroll 0–300px to 0–1; pill width/radius do not change | [Live HTML](https://clerk.com), [header JS](https://clerk.com/_next/static/chunks/23gc8m4ngif4t.js?dpl=dpl_DiBd3WWCMdGL3JjeDM5GREGUwTZT), [utility CSS](https://clerk.com/_next/static/chunks/1tb2cy0f4aqnj.css?dpl=dpl_DiBd3WWCMdGL3JjeDM5GREGUwTZT) |

Three conclusions survive the differences:

1. Geometry is decisive. Raycast and Clerk can use very high fill alpha without reading as a viewport-wide strip because the material has visible air around it ([Raycast](https://www.raycast.com), [Clerk](https://clerk.com)).
2. “Best product site” does not imply “glass”: Vercel, Arc, Stripe, and Framer deliberately use opaque, transparent, or menu-only treatments rather than a continuously blurred header ([Vercel](https://vercel.com), [Arc](https://arc.net), [Stripe](https://stripe.com), [Framer](https://framer.com)).
3. Full-bleed blur can work when the scroll transition is soft and the visual ground is controlled, as on Linear and Resend, but it is precisely the pattern most likely to read as a horizontal band on this near-white page ([Linear](https://linear.app), [Resend](https://resend.com)).

## 5. Realistic-glass CSS toolkit

### Directional inner top highlight

A uniform border says “outlined shape.” A one-pixel inset highlight only on the light-facing edge says “thickness”: it stays inside the silhouette, does not affect layout, and can be stronger on top than on the bottom. Use a quiet border for the full ring, then `inset 0 1px 0` for the optical catch. Multiple inset shadows are also the basis of more elaborate Fresnel-like recreations ([Atlas Pup Labs](https://atlaspuplabs.com/blog/liquid-glass-but-in-css)). For this narrow header, one top hairline and one faint inner ring are enough.

### Saturation boost

Blur averages neighbouring pixels and can turn colourful ground into grey fog. A `saturate(150%–180%)` step restores chroma in the sampled backdrop, so the glass visibly belongs to the sky beneath it. Saturation is much cheaper than blur in the filter-cost comparison, while blur is the slow operation ([web.dev filter performance](https://web.dev/articles/understanding-css?hl=en)). It only helps when the ground actually contains colour; over flat white, saturation has nothing to amplify.

### Noise / grain

Low-alpha static grain breaks perfectly smooth digital gradients and gives the eye a micro-texture associated with a physical material. Microsoft’s Acrylic recipe explicitly combines background, blur, blend, tint, and noise, and warns that the effect is GPU-intensive and should collapse to a solid colour in high-contrast or power-saving contexts ([Microsoft Acrylic material](https://learn.microsoft.com/en-us/windows/apps/design/style/acrylic)).

Recommendation here: if a visual A/B test proves it helps, use one tiny static tiled asset at roughly 1–2% opacity on the existing sheen pseudo-element. Do not animate it. The copy-paste baseline omits grain because the benefit on a 40–50px capsule is marginal and the page already has a rich ground.

### Specular sheen

Place a low-alpha gradient above the tint but below the header content, strongest at the top or top-left and gone before the lower third. It should follow the radius and remain static. A broad white diagonal across the whole component is not a specular cue; it is another pale fill. Apple’s own material uses geometry- and environment-responsive highlights, but the web analogue should be deliberately restrained ([WWDC25 “Meet Liquid Glass”](https://developer.apple.com/videos/play/wwdc2025/219/)).

### The ring pattern

The robust stack is:

1. semi-transparent 1px border for silhouette;
2. inset 1px top highlight for the lit edge;
3. inset 0 0 0 0.5px for a fine internal ring;
4. small contact shadow plus a larger low-alpha lift shadow.

Clerk’s live header is a useful measured example: a 0.5px inset white ring, a 0.5px external dark ring, then three low-alpha shadows ([live Clerk HTML](https://clerk.com)). Raycast uses the simpler dark-mode form: ~6% white border and ~15% inset top highlight ([Raycast header CSS](https://www.raycast.com/_next/static/immutable/chunks/2frcgw3_vp50k.css)).

### SVG `feDisplacementMap` refraction

SVG filter chains can create a displacement texture with `feImage`/`feTurbulence`, warp the sampled image with `feDisplacementMap`, and optionally split colour channels for chromatic aberration. A detailed implementation reports that the effect works only in Chrome and that more than one or two animated surfaces quickly slows the tab ([Atlas Pup Labs](https://atlaspuplabs.com/blog/liquid-glass-but-in-css)).

The CSS grammar accepts `url()` filter references ([MDN backdrop-filter](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/backdrop-filter)), but grammar support is not rendering support. Safari 26’s inability to use URL filters in `backdrop-filter` is tracked as an open WebKit issue, and an adjacent accelerated-filter issue remains reopened ([WebKit bug 297732](https://bugs.webkit.org/show_bug.cgi?id=297732), [WebKit bug 297770](https://bugs.webkit.org/show_bug.cgi?id=297770)). Some community packages claim broader support, but those claims conflict with the browser bug record and are **unverified** for true backdrop refraction.

Verdict: not worth it here. It is non-portable, adds arbitrary GPU filter work, complicates fallback detection, and contributes less to the owner’s complaint than simply removing the full-width band silhouette.

### Dark-mode inversion

Dark glass should not be a literal negative of light glass:

- use a dark blue/charcoal tint around 60–65% for the scrolled island and a clearer 34–38% rest state;
- reduce saturation slightly and add a small `brightness(<100%)` to prevent bright content blooming through;
- make the top rim cool and low-alpha rather than white;
- use a stronger black lift shadow because dark-on-dark separation comes from occlusion more than a pale outline;
- keep labels on stable theme tokens instead of trying to sample and flip every glyph.

That approximates the direction of Apple’s size/environment adaptation without pretending to reproduce its per-pixel light/dark switching ([WWDC25 “Meet Liquid Glass”](https://developer.apple.com/videos/play/wwdc2025/219/)).

### Accessibility and deterministic contrast

- `prefers-reduced-transparency` exists specifically to detect a request to reduce translucent layers, but it remains limited/experimental across the browser set; an application-level fallback must still be robust when the query is unsupported ([MDN](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/At-rules/%40media/prefers-reduced-transparency)).
- `prefers-contrast` has broad support and should replace the glass with an opaque surface and stronger border when set to `more` ([MDN](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/At-rules/%40media/prefers-contrast)). Forced-colours should use system colours and no sheen.
- WCAG contrast is measured against the pixels immediately behind the text. Normal text needs 4.5:1 and large text 3:1; on a varying backdrop, every expected adjacent region must pass, not merely the average tint ([WCAG technique G18](https://www.w3.org/WAI/WCAG20/Techniques/general/G18), [Understanding SC 1.4.3](https://www.w3.org/WAI/WCAG21/Understanding/contrast-minimum.html)).
- Therefore the scrolled fill must be strong enough that worst-case underlying text becomes an unreadable wash, and automated contrast tests should be run over representative light, dark, colourful, and text-heavy content. Blur is not a contrast guarantee.

## 6. Performance under this repository’s budget

### The repository’s own measurements

`tests/test_chat_interface_contract.py:110-124` records a 1440×900 full-document scroll benchmark:

| Moving background layers | Median frame | Frames over 32ms |
|---:|---:|---:|
| 3 | 33.3ms | 95 |
| 2 | 16.7ms | 76 |
| 1 | 16.7ms | 27 |
| 0 | 16.7ms | 29 |

The current stylesheet contains 40 unprefixed and 40 prefixed backdrop-filter declarations (80 declarations total), although the final cascade disables many of the cheaper-looking nested surfaces. The contract allows at most one moving background layer and currently spends none. It also bans large background `filter: blur(...)` and explains that any moving backdrop is charged again by every glass surface above it. The final rendering section clamps general glass to 14px, disables soft/nested filters, and reserves 22px for the smaller header capsule (`styles.css:2445-2486`). This local measurement is more relevant than a generic desktop demo.

### Cost model

- A backdrop filter requires the engine to render what is behind the element, filter that image, then composite it. WebKit explicitly warns that this forces extra rendering passes and should be used only where necessary ([WebKit](https://webkit.org/blog/3632/introducing-backdrop-filters/)); web.dev likewise warns that `backdrop-filter` may harm performance ([web.dev](https://web.dev/articles/backdrop-filter?hl=en)).
- Cost scales with filtered area, invalidation frequency, device pixel ratio, filter radius, and layer count. A sticky header over scrolling content cannot reuse a permanently static filtered bitmap because the sampled pixels change each frame.
- Blur is the expensive filter. A simple neighbourhood model grows roughly with the square of radius—doubling radius considers about four times as many nearby pixels—although modern GPU/separable implementations can change the exact curve ([web.dev](https://web.dev/articles/understanding-css?hl=en)). The practical instruction is still to use the smallest acceptable radius.
- Each progressive-blur layer adds another backdrop-filter/compositor pass. A mask controls the visibility of the filtered result; it should not be assumed to make the upstream blur free. This is an inference from the filter/compositing pipeline, and exact allocation differs by engine ([Filter Effects Level 2](https://drafts.csswg.org/filter-effects-2/), [WebKit pipeline](https://webkit.org/blog/3632/introducing-backdrop-filters/)).
- Masks, filters, opacity, and `will-change` can create backdrop roots or compositing layers. Excess layers consume memory and can be slower than repainting a small region ([MDN backdrop roots](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/backdrop-filter), [Chrome rendering layers](https://developer.chrome.com/blog/inside-browser-part3)).
- `will-change` is not a general blur accelerator. It can waste resources, and it should be applied only shortly before a real transform/opacity animation, then removed ([web.dev](https://web.dev/articles/animations-and-performance)). Do not leave `will-change: backdrop-filter` on this sticky header.
- `contain: paint` can prevent descendants from painting outside a component and skip their paint when the whole component is offscreen, but it also clips overflow and creates a new containing block and stacking context. It does not stop a visible backdrop-filter from sampling the scrolling pixels behind it ([MDN contain](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/contain)). It is not a magic win for this always-visible sticky island.

### Affordability decision

| Technique | Budget decision | Reason |
|---|---|---|
| One 20px blur on the small `.bar-island` | **Affordable; keep** | Already the intended exception, much smaller than a full-width strip. |
| `saturate()` and optional dark `brightness()` in the same filter list | **Affordable** | Small incremental colour operation beside the blur. |
| Static border, inset ring, shadow, static sheen pseudo | **Affordable** | Paint-only and not continuously animated. |
| Scroll transition of fill/border/shadow | **Affordable with restraint** | No layout or changing blur kernel; background/shadow still repaint, so keep the transition short. |
| Static 1–2% tiled grain | **Probably affordable, optional** | No backdrop pass and no animation, but marginal value on a small header. |
| One extra masked constant-blur trailing edge | **Borderline; do not ship by default** | Adds a second backdrop pass on every scroll frame. |
| Two-layer pseudo progressive blur | **Too expensive for this page** | Doubles the header backdrop work for a subtle gain. |
| Five-, seven-, or twelve-layer progressive blur | **Reject** | Five to twelve extra filtered/composited surfaces; conflicts with the page’s explicit restraint. |
| 40–64px blur layer | **Reject** | Large kernel and enlarged sampled region. |
| SVG displacement/refraction/chromatic aberration | **Reject** | Non-portable arbitrary filter chain with significant GPU cost. |
| Animated noise, sheen, filter, width, or radius | **Reject** | Adds continuous paint/layout/filter invalidation; an animated sheen/noise also consumes the one-moving-layer posture. |

## Copy-paste CSS spec

This block is written for the **current** markup: `.bar` remains a transparent geometry shell and `.bar-island` owns the material. The existing token names are scoped on the island so the rest of the site’s surface system is not accidentally retuned. It deliberately uses one backdrop-filtered element, one static pseudo-element, no masks, no SVG filter, and no keyframes.

```css
/* Header-only Liquid-Glass approximation. Paste after the existing header rules. */
.bar {
  /* Required by the repository contract: the viewport-wide shell paints nothing. */
  background: none;
  border: 0;
  box-shadow: none;
  -webkit-backdrop-filter: none;
  backdrop-filter: none;
}

.bar-island {
  /* Scope the established surface tokens to the header only. */
  --g-fill-bar: rgba(235, 246, 255, 0.58);
  --g-fill-bar-rest: rgba(255, 255, 255, 0.32);
  --g-edge: rgba(255, 255, 255, 0.58);
  --g-blur: blur(20px) saturate(175%);
  --g-shadow:
    0 2px 6px rgba(20, 70, 140, 0.08),
    0 14px 34px rgba(20, 70, 140, 0.16);
  --g-spec: linear-gradient(
    176deg,
    rgba(255, 255, 255, 0.62) 0%,
    rgba(255, 255, 255, 0.10) 42%,
    transparent 72%
  );

  position: relative;
  isolation: isolate;
  background: var(--g-fill-bar);
  border: 1px solid var(--g-edge);
  -webkit-backdrop-filter: var(--g-blur);
  backdrop-filter: var(--g-blur);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.78),
    inset 0 0 0 0.5px rgba(117, 170, 224, 0.14),
    var(--g-shadow);
  transition:
    background-color 180ms var(--ease),
    border-color 180ms var(--ease),
    box-shadow 180ms var(--ease);
}

html[data-theme="dark"] .bar-island {
  --g-fill-bar: rgba(9, 20, 34, 0.64);
  --g-fill-bar-rest: rgba(22, 43, 68, 0.36);
  --g-edge: rgba(176, 214, 255, 0.22);
  --g-blur: blur(20px) saturate(160%) brightness(92%);
  --g-shadow:
    0 2px 6px rgba(0, 0, 0, 0.36),
    0 16px 38px rgba(0, 0, 0, 0.52);
  --g-spec: linear-gradient(
    176deg,
    rgba(196, 225, 255, 0.18) 0%,
    rgba(196, 225, 255, 0.03) 44%,
    transparent 72%
  );

  box-shadow:
    inset 0 1px 0 rgba(196, 225, 255, 0.20),
    inset 0 0 0 0.5px rgba(176, 214, 255, 0.10),
    var(--g-shadow);
}

/* Scroll-top: clearer material and a contact shadow, but still readable. */
html.reveals .bar:not(.solid) .bar-island {
  background: var(--g-fill-bar-rest);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.72),
    inset 0 0 0 0.5px rgba(117, 170, 224, 0.10),
    0 2px 10px rgba(20, 70, 140, 0.07);
}

html[data-theme="dark"].reveals .bar:not(.solid) .bar-island {
  box-shadow:
    inset 0 1px 0 rgba(196, 225, 255, 0.16),
    inset 0 0 0 0.5px rgba(176, 214, 255, 0.08),
    0 3px 12px rgba(0, 0, 0, 0.30);
}

/* Scrolled: increase separation, not geometry or blur. */
.bar.solid .bar-island {
  background: var(--g-fill-bar);
  border-color: var(--g-edge);
}

.bar-island::before {
  content: "";
  position: absolute;
  inset: 0;
  z-index: 0;
  border-radius: inherit;
  background: var(--g-spec);
  opacity: 0.72;
  pointer-events: none;
}

.bar-island > .bar-left,
.bar-island > .bar-right {
  position: relative;
  z-index: 1;
}

/* Opaque, deterministic fallbacks. */
@supports not ((-webkit-backdrop-filter: blur(1px)) or (backdrop-filter: blur(1px))) {
  .bar-island {
    background: var(--paper);
    -webkit-backdrop-filter: none;
    backdrop-filter: none;
  }
}

@media (prefers-reduced-transparency: reduce) {
  .bar-island,
  html.reveals .bar:not(.solid) .bar-island,
  .bar.solid .bar-island {
    background: var(--paper);
    -webkit-backdrop-filter: none;
    backdrop-filter: none;
  }

  .bar-island::before { opacity: 0.10; }
}

@media (prefers-contrast: more) {
  .bar-island,
  html.reveals .bar:not(.solid) .bar-island,
  .bar.solid .bar-island {
    background: var(--paper);
    border-color: var(--ink);
    -webkit-backdrop-filter: none;
    backdrop-filter: none;
    box-shadow: none;
  }

  .bar-island::before { display: none; }
}

@media (forced-colors: active) {
  .bar-island {
    color: CanvasText;
    background: Canvas;
    border: 1px solid CanvasText;
    -webkit-backdrop-filter: none;
    backdrop-filter: none;
    box-shadow: none;
  }

  .bar-island::before { display: none; }
}

@media (prefers-reduced-motion: reduce) {
  .bar-island { transition: none; }
}
```

**Budget flags:** the block above does not add an animated background layer and keeps the already-budgeted single island blur. Adding a masked `::after` would create a second live backdrop-filter pass; adding five-to-twelve progressive layers, SVG displacement, animated grain, or an animated sheen would break the intended rendering posture and should not be appended to this specification.
