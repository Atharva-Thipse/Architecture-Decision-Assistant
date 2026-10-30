# User Interface Knowledge Base

This file covers **user-facing interface decisions**: which type of
interface a component should expose to end users (web, mobile, desktop,
CLI, voice, or none), and which frontend tech stack to use within that
choice. Backend API architecture and tech stack (REST/gRPC/SOAP, backend
language/framework) is a separate decision — see `apis.md`.

This decision is made **per component that has an end-user-facing
surface** — not every component needs one (see "No UI / Headless" below).

## How to use this file (for the orchestrator/LLM)

**Step 1** — determine the interface category using the decision drivers
below. **Step 2** — once the category is settled, use the matching
sub-section to pick a specific tech stack.

Decision drivers to gather (infer where possible, ask only what's unclear):

- **Target audience & device**: general public on the web, internal tool users, mobile-first audience, developers/power users, embedded/kiosk use?
- **Need for native device features**: camera, GPS, push notifications, biometrics, offline-first storage, background processing?
- **App store distribution**: is being listed in the App Store/Play Store required or desired?
- **Performance/interactivity needs**: highly interactive real-time UI, or mostly content display/forms?
- **SEO/discoverability**: does this need to be indexed by search engines?
- **Team skillset**: existing frontend/mobile expertise, willingness to learn a new stack
- **Time-to-market & budget**: cross-platform code reuse vs best-in-class native experience
- **Offline capability**: must the interface work with no/intermittent network access?
- **Accessibility requirements**: any regulatory/compliance accessibility needs (WCAG, etc.)
- **Multi-platform reach**: does this need to work on web + mobile + desktop simultaneously, and if so, is shared code valuable?

---

## Category: Web Application

**Use when:** broadest possible reach without installation, need to be accessible from any device with a browser, need SEO/discoverability, internal business tools, dashboards
**Avoid when:** need deep native device integration (camera, GPS, biometrics) that browsers can't provide well, need offline-first behavior beyond what PWAs offer, need App Store presence specifically

### Option: Traditional Server-Rendered (Multi-Page App)

**Use when:** content-heavy sites, need strong SEO out of the box with minimal effort, team is more backend-focused, interactivity needs are modest (mostly forms/navigation)
**Avoid when:** need a highly interactive, app-like experience with frequent client-side state updates

**Tech stack examples:** Django templates + Python, Ruby on Rails views, ASP.NET MVC/Razor, PHP (Laravel Blade)

**Trade-offs:** Simple mental model, SEO-friendly by default, less client-side JavaScript complexity. Full-page reloads on navigation feel less fluid than an SPA; harder to build rich, app-like interactions.

**Common pitfalls:** Bolting on heavy client-side JavaScript piecemeal until it becomes an unmanaged hybrid without the benefits of either approach.

**Notes:** Well suited to small teams or content-focused sites where simplicity outweighs the need for rich interactivity.

---

### Option: Single Page Application (SPA)

**Use when:** need a highly interactive, app-like experience, frontend and backend teams are separate (clean API boundary), real-time UI updates are common
**Avoid when:** SEO is critical and the team isn't prepared to invest in additional SSR/prerendering tooling, initial load performance is a major concern and the team can't invest in code-splitting/optimization

**Tech stack examples:** React, Vue, Angular, Svelte (as client-side-only apps, e.g. via Vite/Create React App)

**Trade-offs:** Rich, app-like interactivity; clean separation between frontend and backend via APIs. Weaker SEO by default (mitigated with extra tooling), larger initial JavaScript bundle, more client-side state management complexity.

**Common pitfalls:** Not addressing SEO/initial-load performance early if they turn out to matter; letting client-side state management grow unmanaged without a clear pattern (Redux, Zustand, Pinia, etc. — pick one deliberately).

**Notes:** React has the largest ecosystem/hiring pool; Vue is often considered easier to onboard; Angular is more opinionated/batteries-included (common in larger enterprise teams); Svelte compiles away framework overhead for smaller bundle sizes.

---

### Option: Server-Side Rendered / Hybrid Framework

**Use when:** want SPA-like interactivity but also need good SEO and fast initial page loads, want a more opinionated full-stack structure
**Avoid when:** the added framework complexity and hosting requirements aren't justified by a simple internal tool

**Tech stack examples:** Next.js (React), Nuxt (Vue), SvelteKit (Svelte), Remix (React)

**Trade-offs:** Best of both worlds — SEO-friendly server rendering plus rich client-side interactivity after hydration. More moving parts than a plain SPA or plain server-rendered app; hosting often needs to support server-side rendering (not just static file hosting).

**Common pitfalls:** Not understanding the hydration model well enough, leading to subtle bugs where server-rendered and client-rendered output diverge.

**Notes:** This is increasingly the default recommendation for new public-facing web apps that need both interactivity and SEO.

---

### Option: Static Site

**Use when:** content is mostly static or changes infrequently (marketing sites, documentation, blogs), want the simplest, cheapest, fastest hosting (CDN-only, no server needed)
**Avoid when:** content is highly dynamic or personalized per user, need real-time data

**Tech stack examples:** Astro, Hugo, Jekyll, Eleventy, or plain HTML/CSS

**Trade-offs:** Extremely fast, cheap to host (static file CDN), minimal attack surface. Not suited to dynamic, personalized, or frequently-changing content without a rebuild/redeploy.

**Notes:** Often paired with a headless CMS for content editing without redeploying code for every change.

---

## Category: Mobile Application

**Use when:** audience is primarily mobile, need native device features (camera, GPS, push notifications, biometrics), need App Store/Play Store presence, need best-in-class performance for mobile-specific interactions
**Avoid when:** a responsive web app or PWA would meet the actual needs without the overhead of app store distribution and mobile-specific development/release cycles

### Option: Native (Platform-Specific)

**Use when:** need the absolute best performance and platform-idiomatic feel, need access to the newest platform APIs immediately on release, team has (or is willing to build) separate iOS and Android expertise
**Avoid when:** budget/team size doesn't support maintaining two separate codebases, time-to-market favors a single cross-platform codebase

**Tech stack examples:** Swift/SwiftUI (iOS), Kotlin/Jetpack Compose (Android)

**Trade-offs:** Best performance and platform fidelity, immediate access to new OS features. Requires maintaining two separate codebases and two sets of platform expertise — highest cost and slowest cross-platform time-to-market.

**Notes:** Common choice when the app is performance-critical (e.g. games, AR/VR, heavy media processing) or when platform-specific UX fidelity is a core product requirement.

---

### Option: Cross-Platform Framework

**Use when:** want to ship to iOS and Android from a largely shared codebase, team is small or wants to avoid maintaining two native codebases, app doesn't push the absolute limits of platform-specific performance
**Avoid when:** app has heavy platform-specific performance needs (e.g. complex animations, AR, intensive graphics) where native would meaningfully outperform

**Tech stack examples:** React Native, Flutter, .NET MAUI

**Trade-offs:** Significant code reuse across platforms, faster time-to-market, smaller team can cover both platforms. Some performance overhead vs native, occasional friction accessing brand-new platform-specific APIs immediately on release, "native-feeling" polish sometimes takes extra effort.

**Common pitfalls:** Assuming 100% code reuse is achievable — some platform-specific code is almost always still needed for polish or specific integrations.

**Notes:** Flutter tends to offer more UI consistency across platforms (renders its own UI layer); React Native leans on native components and has a larger existing JS ecosystem/talent pool if the team already knows React.

---

### Option: Progressive Web App (PWA)

**Use when:** want app-like mobile experience (installable, offline-capable, push notifications on supporting platforms) without app store distribution, budget/timeline doesn't support native or cross-platform mobile development
**Avoid when:** need deep native features not well-supported by web APIs (varies by platform, notably more limited on iOS), App Store presence is a hard requirement

**Trade-offs:** Single codebase shared with the web app, no app store approval process, installable and can work offline. More limited access to native device features than native/cross-platform apps, especially on iOS; no app store discoverability.

**Notes:** A good incremental step — start as a PWA and graduate to native/cross-platform later if native feature needs grow.

---

## Category: Desktop Application

**Use when:** need a rich, installed application experience on Windows/Mac/Linux, need offline-first behavior with local file system or hardware access beyond what a browser allows
**Avoid when:** a web app would serve the same purpose without requiring installation and updates

### Option: Web-Based Desktop Wrapper

**Use when:** want to reuse existing web app code/skills to ship a desktop app, need cross-platform (Windows/Mac/Linux) coverage without separate native codebases
**Avoid when:** resource usage (memory footprint) is a significant concern, need the leanest possible native performance

**Tech stack examples:** Electron, Tauri

**Trade-offs:** Reuses web development skills/code, genuinely cross-platform from one codebase. Electron apps are known for relatively high memory usage; Tauri (using the OS's native webview) is significantly lighter but a younger ecosystem.

**Common pitfalls:** Not evaluating Tauri as a lighter alternative to Electron when resource usage matters.

**Notes:** Electron is the more mature/battle-tested choice with a huge ecosystem (VS Code, Slack, Discord all use it); Tauri trades some ecosystem maturity for a much smaller footprint.

---

### Option: Native Desktop

**Use when:** need the best possible performance and OS integration, targeting primarily a single OS, need access to OS-specific APIs not well exposed to web wrappers
**Avoid when:** need cross-platform reach without maintaining separate codebases per OS

**Tech stack examples:** .NET/WPF or WinUI (Windows), Swift/AppKit (macOS), Qt (cross-platform C++)

**Trade-offs:** Best performance and OS integration. Higher development cost, especially for genuine cross-platform coverage (Qt helps here but still has a learning curve).

**Notes:** Qt is worth calling out specifically as a mature cross-platform native option when Electron/Tauri's web-based approach isn't desired but true cross-platform native reach still is.

---

## Category: Command-Line Interface (CLI)

**Use when:** target users are developers/technical/power users, tool is meant to be scripted or used in automation pipelines, need something lightweight with no UI overhead
**Avoid when:** target audience is non-technical end users who need a graphical experience

**Tech stack examples:** Python (`argparse`, `click`, or `typer`), Node.js (`commander`, `oclif`), Go (`cobra`), Rust (`clap`)

**Trade-offs:** Fast to build, easy to script/automate, low resource overhead, easy to distribute as a single binary (especially with Go/Rust). No visual affordances for non-technical users; discoverability of features relies on good `--help` documentation.

**Common pitfalls:** Poor error messages and inconsistent flag naming conventions, which matter a lot for CLI usability even though there's "no UI" in the visual sense.

**Notes:** Go and Rust are strong choices when easy single-binary distribution matters (no runtime dependency to install); Python/Node are strong choices when the team already has that language elsewhere in the stack and startup-time/binary-size is less critical.

---

## Category: Voice / Conversational Interface

**Use when:** hands-free or eyes-free interaction is a core requirement (smart speakers, in-car, accessibility-focused use cases), interaction is naturally conversational
**Avoid when:** the interaction is naturally visual/spatial (most business applications) — voice adds novelty but little value for typical CRUD/dashboard use cases

**Tech stack examples:** Amazon Alexa Skills Kit, Google Assistant Actions, custom conversational AI (Rasa, Dialogflow, or an LLM-based custom assistant)

**Trade-offs:** Enables hands-free/accessible interaction. Much harder to convey complex information (no persistent visual reference), design and testing require different skills than visual UI design, discoverability of what the assistant "can do" is a real UX challenge.

**Notes:** Usually a supplementary interface (alongside a web/mobile app) rather than the sole interface, except in specific hands-free-first product categories.

---

## Category: No UI / Headless

**Use when:** the component is a backend service, integration, or data pipeline with no direct end-user interaction — it's consumed by other components via API only
**Avoid when:** there's actually a human end user who needs to interact with this component directly

**Trade-offs:** No frontend development/maintenance cost at all for this component. Not applicable if a human user genuinely needs to interact with it.

**Notes:** This is a legitimate and common answer — not every component needs a UI. Pair with `apis.md` to define how this component is consumed by others.

---

## Decision Summary Guidance (for the orchestrator)

| Signal | Leans toward |
|---|---|
| Broad reach, any device, SEO matters | Web Application (SSR/Hybrid if interactivity also needed) |
| Highly interactive app-like web experience, SEO less critical | Single Page Application |
| Content-heavy, infrequent updates | Static Site |
| Mobile-first, need native features, budget for 2 codebases | Native Mobile |
| Mobile-first, want shared codebase | Cross-Platform Mobile |
| Mobile-like experience without app store overhead | Progressive Web App |
| Installed cross-platform app, team knows web dev | Web-Based Desktop Wrapper (Electron/Tauri) |
| Installed app, need best OS-specific performance | Native Desktop |
| Technical/developer audience, scriptable tool | CLI |
| Hands-free/eyes-free interaction is core to the product | Voice/Conversational |
| Component has no direct human user | No UI / Headless |

Remember this decision is made **per component** — a system commonly has
a web app for end users, a CLI for internal ops/admin tasks, and several
headless backend components, all coexisting. Record the interface
decision per component in the decision log, then move to `apis.md` to
decide how components (and, if relevant, this UI) communicate with the
backend.
