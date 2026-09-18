# Naming: searchability / uniqueness audit

Date: 2026-09-18. Single criterion: can a user Google the name, land on the GitHub repo, and
`pip install` it without collisions. Nothing here is about taste.

Every registry / search claim below was fetched live on the date above; the URL is the
citation. Where a fetch failed or an engine served junk, it says so.

## Summary

- **`forge` is un-findable.** GitHub has 158,399 repos with "forge" in the name (top five: 13k,
  13k, 10k, 7.8k, 7.7k stars); PyPI `forge` is a Django tool, npm `forge` a build system, crates
  `forge` a bare-metal tool; every search engine's page one is Minecraft Forge / CurseForge / Forge
  of Empires. `forged`, `forge-mcp` are also all taken (PyPI+npm+crates for `forged`; PyPI+npm for
  `forge-mcp`, and `forge-mcp` on the web is Atlassian's Forge MCP server). Severity: **HIGH**.
- **"forge companion" is free on every registry but hopeless on the web.** `forge-companion`
  is 404 on PyPI, npm and crates.io and has 0 exact-name GitHub repos, yet a web search returns the
  Minecraft "Companion (Neo/Forge)" mod, the "FoE Companion" app, Sunless Skies' "Forged
  Companion", and, with quotes, a *literal gas blacksmith forge* called "ForgeMaster Companion".
  The two words are each hugely overloaded, so the phrase never wins. Severity: HIGH (web) / NONE
  (registries).
- **Almost every single blacksmith word is taken three times over** (smithy, anvil, bellows,
  tongs, quench, slag, ingot, swage: PyPI+npm+crates all 200). Smith-god names are mostly branded
  already (Sindri = Sindri Labs SDKs on all three registries; Brokkr = 351-star Samsung flasher +
  three registries; Mímir = Grafana Mimir; Hephaestus = 1.2k-star agent framework; Vulcan = 7.9k
  stars; Ilmarinen = Finnish pension giant). `Ornith` and `Jev` are the brands of the two models
  this project *depends on* (ornith.ai / typesafe.ai) and are off the table.
- **Clean names found** (PyPI, npm, crates all 404; GitHub exact-name hits at most a few dead
  repos; `.dev` and `.sh` NXDOMAIN): `forgepet`, `anvilpet`, `smithpet`, `forgebuddy`
  (registries only, forgebuddy.com is a Laravel Forge tool), `forgemate`, `hearthsmith`,
  `goibniu`, `hammerfall` (registries only; it is a metal band on the web), `tuyere` (crates
  taken by a 1-star TUI framework).
- **Prior art in this exact niche is crowded and uses "pet"/"buddy"/"companion" generically:**
  `clawd-on-desk` (6.2k stars, "A pixel desktop pet that watches Claude Code, Codex, Cursor"),
  `petdex` (4.1k), `pixel-agents` (9.3k), `agentpet` (357), `oc-claw` (351), `claude-code-tamagotchi`
  (436), `claude-desktop-buddy` (2.6k, Anthropic's own). A name that carries "pet" is on-category
  for search intent; a name that carries "forge" alone is invisible.
- **Pick: `forgepet`.** PyPI/npm/crates 404, 2 GitHub name-hits (a 1-star 2021 Minecraft repo and
  nothing else), `forgepet.dev` / `forgepet.sh` unregistered, and a web search for the one word
  returns only a personal blog and a pet-washing gadget called "Pet Forge". It is spellable from
  hearing it, keeps the blacksmith lineage (`forgepet`, `forgepetd`, `forgepet-mcp`), and
  matches how people actually search this category ("desktop pet", "claude code pet").
  Runner-up if a proper name is wanted for the character himself: **Goibniu** (Irish smith-god;
  every registry free, 6 GitHub repos, domains free, page one is only Wikipedia/Britannica), with
  the caveat that nobody can spell it from hearing it, which is the one thing the criterion cares
  about.

## Method and caveats

- Registries: `https://pypi.org/pypi/<name>/json`, `https://registry.npmjs.org/<name>`,
  `https://crates.io/api/v1/crates/<name>` via `curl`; 200 = taken, 404 = free.
- GitHub: `gh api search/repositories?q=<name>+in:name&sort=stars` (authenticated). `total_count`
  is GitHub's count of repos whose *name contains* the term, so it overcounts substrings
  (`forgehand` matches `ForgeHandshake`, `smithpet` matches `smithpeter`). Top hits are listed so
  you can see whether the count is real.
- Web: **DuckDuckGo returned a bot CAPTCHA for every request** (`https://html.duckduckgo.com/html/?q=…`
  and `lite.duckduckgo.com`; the page contains 67 `anomaly` markers and no results), including
  through WebFetch. **Bing** via WebFetch worked for `forge`, `forged`, `forge-mcp`, `forge companion`,
  `hearthsmith`, `brokkr` but served unrelated cached SERPs (Roblox, Surfshark, Portuguese
  "outono", a Brazilian accounting portal) for `forgehand`, `goibniu`, `sindri`, `ilmarinen`,
  `volund`; those Bing readings are discarded. **Brave Search** (`https://search.brave.com/search?q=…`)
  returned parseable organic results for every query and is the web source used in the tables.
- Domains: `getent hosts <name>.dev|.sh` then HTTPS GET. "NXDOMAIN" means no DNS record, which is a
  strong (not certain) signal it is unregistered; "resolves" means someone owns it.

## 1. Current names and the proposed one

| name | PyPI | npm | crates.io | GitHub `in:name` (top hit) | Brave top 3 | severity |
|---|---|---|---|---|---|---|
| `forge` | 200 — "Quickly build a professional web app using Django" v0.22.0 ([pypi](https://pypi.org/project/forge/)) | 200 — "A no customization 'build' system" ([npm](https://registry.npmjs.org/forge)) | 200 — bare-metal project tool, 5.6k dl ([crates](https://crates.io/crates/forge)) | **158,399**; lllyasviel/stable-diffusion-webui-forge 13,020, InsForge/InsForge 13,001, conda-forge/miniforge 10,202, MinecraftForge 7,785, vercel/next-forge 7,671 ([search](https://github.com/search?q=forge+in%3Aname&type=repositories)) | files.minecraftforge.net; forge-vtt.com; curseforge.com ([brave](https://search.brave.com/search?q=forge)) | **HIGH** |
| `forge companion` / `forge-companion` | 404 ([pypi](https://pypi.org/project/forge-companion/)) | 404 ([npm](https://registry.npmjs.org/forge-companion)) | 404 ([crates](https://crates.io/crates/forge-companion)) | 31 for the two words, 0 for `forgecompanion`; top: ARC-Forge-Ultimate-Companion-App-for-ARC-Raiders ★7, exquest/CompanionForge ★3 ("AI thinking companion for Claude or ChatGPT"), HexForge-Companion ★1 ([search](https://github.com/search?q=forge+companion+in%3Aname&type=repositories)) | curseforge.com "Companion 🐕 (Neo/Forge)" mod; curseforge.com "Companions!"; modrinth.com "[FORGE] Companions!"; then play.google.com "FoE Companion" ([brave](https://search.brave.com/search?q=forge+companion)). Quoted `"forge companion"`: blacksmithsdepot.com "ForgeMaster Gas Blacksmiths Forge Companion Model", forum.atlas-games.com ([brave](https://search.brave.com/search?q=%22forge+companion%22)). Bing agrees: minecraftforge.net ×2, curseforge.com ×2, forgeofempires.com ([bing](https://www.bing.com/search?q=forge+companion)) | **HIGH** on the web, NONE on registries |
| `forged` | 200 — "Tools for data generation" (OtoSense) ([pypi](https://pypi.org/project/forged/)) | 200 — empty v0.0.1 ([npm](https://registry.npmjs.org/forged)) | 200 — "Client API for forged.dev", 9.7k dl ([crates](https://crates.io/crates/forged)) | 1,681; ReTerraForged ★420 ([search](https://github.com/search?q=forged+in%3Aname&type=repositories)) | forged.com (clothing brand); forged4x4.com; merriam-webster.com ([brave](https://search.brave.com/search?q=forged)) | **HIGH** |
| `forge-mcp` | 200 — "pytest for MCP servers", author @mcpforge.dev ([pypi](https://pypi.org/project/forge-mcp/)) | 200 — "MCP server for the Hermes Forge platform" v2.3.0, modified 2026-05 ([npm](https://registry.npmjs.org/forge-mcp)) | 404 | 436; IBM/mcp-context-forge ★4,492, mlzoo/mcp_forge ★208, goern/forgejo-mcp ★135 ([search](https://github.com/search?q=forge-mcp+in%3Aname&type=repositories)) | go.atlassian.com "Forge MCP Server"; github.com tyler-technologies-oss/forge-mcp; forgecode.dev; mcpforge.org ([brave](https://search.brave.com/search?q=forge-mcp)) | **HIGH** |
| `forge-sprite` | 404 | 404 | 404 | 119; 0x0funky/agent-sprite-forge ★4,139 ([search](https://github.com/search?q=forge-sprite+in%3Aname&type=repositories)) | github.com agent-sprite-forge; store.steampowered.com "Sprite Forge"; spriteforge.tech ([brave](https://search.brave.com/search?q=forge-sprite)) | MEDIUM (registries free, web owned by Sprite Forge the game + a 4k-star agent skill) |

Domains: `forge.dev` and `forge.sh` both resolve and serve HTTP 200; `forgecompanion.dev/.sh`
and `forge-companion.dev/.sh` are NXDOMAIN.

## 2. Candidate audit

Legend: registry cell = HTTP status and what sits there. GH = `total_count` for `<name> in:name`
plus the top hit. Brave = top three organic domains/titles. Sev = NONE / LOW / MEDIUM / HIGH.

### 2a. Blacksmith vocabulary

| name | PyPI | npm | crates | GH | Brave top 3 | .dev / .sh | Sev |
|---|---|---|---|---|---|---|---|
| smithy | 200 "Python port of Ruby Rake" | 200 CSS/JS preprocessor | 200 web framework, 14k dl | 896; smithy-lang/smithy ★2,360 (AWS IDL), smithy-rs ★677 | smithey.com; smithy.com; merriam-webster ([brave](https://search.brave.com/search?q=smithy)) | — | **HIGH** (AWS Smithy) |
| anvil | 200 cloud task runner | 200 | 200 templating, 17k dl | 4,466; anvil-ui/anvil ★1,437, square/anvil ★1,402, anvil-works/anvil-runtime ★1,025 | wikipedia; blacksmithsdepot.com; nctoolco.com ([brave](https://search.brave.com/search?q=anvil)) | — | **HIGH** |
| bellows | 200 "Library implementing EZSP" (zigpy) | 200 accordion UI | 200 task framework, updated 2026-09 | 220; zigpy/bellows ★219 | wikipedia; merriam-webster; bellowsafs.com ([brave](https://search.brave.com/search?q=bellows)) | — | HIGH (Home Assistant users know zigpy/bellows) |
| tongs | 200 "Multi-forge MR/CI TUI" | 200 cookie util | 404 | 545; Tongsuo ★1,535 (substring) | wikipedia; seriouseats.com; all-clad.com ([brave](https://search.brave.com/search?q=tongs)) | — | HIGH (kitchen-utensil SERP) |
| quench | 200 arrangement engine | 200 | 200 programming language, 11k dl | 605; quenchjs ★117 | quench.culligan.com; merriam-webster; dictionary.com ([brave](https://search.brave.com/search?q=quench)) | — | HIGH (Culligan Quench) |
| slag | 200 blockchain microblog | 200 logger | 200 rust dialect | 1,036; mystor/slag ★73 | wikipedia; merriam-webster; usgs.gov ([brave](https://search.brave.com/search?q=slag)) | — | HIGH (dictionary word) |
| ingot | 200 PyQt boilerplate | 200 automation platform | 200 packet parsing, **237k dl**, updated 2026-07 | 468; liuweichaox/Ingot ★52 | merriam-webster; wikipedia; ingot.io ([brave](https://search.brave.com/search?q=ingot)) | — | HIGH |
| hammerfall | 404 | 404 | 404 | 18; Hammerfall-RPG-Helper ★2 (2009) | en.wikipedia.org "HammerFall" (band); hammerfall.net; facebook.com/hammerfall ([brave](https://search.brave.com/search?q=hammerfall)) | NXDOMAIN / NXDOMAIN | HIGH on web (Swedish metal band owns every result), NONE on registries |
| forgehand | 404 | 404 | 404 | 13; ForgeHandshake ★6 (substring), lucianoon/forgehand ★1 (LangGraph multi-agent, 2026-09), akovanda/forgehand ★0 (local-LLM coding worker, 2026-07) | reddit r/wow; wowhead "Forgehand Phillo" NPC; vanguardguild.fandom ([brave](https://search.brave.com/search?q=forgehand)) | **forgehand.dev resolves, HTTP 200** — `<title>Forgehand</title>`, "The tests passed. The code didn't. A coding agent hid fields… Forgehand reads every pull request" — an AI-coding-agent product in the same space / .sh NXDOMAIN | **MEDIUM-HIGH** (live AI-dev product on the exact name) |
| hearthsmith | 404 | 404 | 404 | 3; all ★0 (two StackBlitz stubs, one "cozy city builder" 2026-09) | heartsmith.com (jewelry — engine auto-corrects the spelling), trustpilot heartsmith, tiktok heartsmith ([brave](https://search.brave.com/search?q=hearthsmith)); Bing: heartsmith.com, heartsmith.app, then hearthsmith.com ([bing](https://www.bing.com/search?q=hearthsmith)) | NXDOMAIN / NXDOMAIN; **hearthsmith.com is a live DIY/home-décor store** ("Discover HearthSmith — your destination for DIY, home repair, cozy living") | LOW-MEDIUM (registries clean; web fights "heartsmith" and a retail site) |
| farrier | 200 "agent-neutral prompt library → Codex/Claude" v3.0.0 | 200 "coding-agent harness" v0.7.0, 2026-08 | 404 | 88; nothing >2★ | wikipedia; merriam-webster; americanfarriers.org ([brave](https://search.brave.com/search?q=farrier)) | — | MEDIUM-HIGH (both PyPI and npm are *active AI-coding-agent tools* named farrier) |
| tuyere | 404 | 404 | 200 "TUI framework based on The Elm Architecture", 290 dl, 2025-12 | 15; moltenlabs/tuyere ★1 (that TUI framework) | wikipedia; merriam-webster; thermafab.com ([brave](https://search.brave.com/search?q=tuyere)) | NXDOMAIN / NXDOMAIN | LOW (only metallurgy pages; one 1-star crate). Unpronounceable to most (twee-YAIR) |
| swage | 200 placeholder | 200 swagger→excel | 200 Rowhammer framework, 2026-01 | 522; awslabs/swage ★23 | wikipedia "Swaging"; merriam-webster ([brave](https://search.brave.com/search?q=swage)) | — | MEDIUM-HIGH |

### 2b. Smith-gods, folk smiths, invented names

| name | PyPI | npm | crates | GH | Brave top 3 | .dev / .sh | Sev |
|---|---|---|---|---|---|---|---|
| wayland / weyland | 200 empty / 200 regex lib | 200 gRPC tool / 200 Durandal optimizer | 200 Wayland bindings / 404 | 2,718; waylandcraft ★2,876 | "wayland smith": wikipedia; whiterose thesis; ancient-origins.net ([brave](https://search.brave.com/search?q=wayland+smith)) | — | **HIGH** — `wayland` is the Linux display protocol; on this user's own desktop stack the word is already spoken for |
| volund / völund | 404 | 200 empty (2021) | 404 | 109; volundr ★32 | volundmfg.com (propulsion company); thewarriorlodge.com; inheritance.fandom ([brave](https://search.brave.com/search?q=volund)) | **volund.dev resolves HTTP 200** / .sh NXDOMAIN | MEDIUM |
| brokkr | 200 data-ingest client | 200 job processor | 200 task queue, 3.6k dl | 99; Gabriel2392/brokkr-flash ★351 (Samsung flasher, active) | wikipedia; godofwar.fandom; github brokkr-flash ([brave](https://search.brave.com/search?q=brokkr)); Bing same ([bing](https://www.bing.com/search?q=brokkr)) | brokkr.dev resolves (403) / NXDOMAIN | HIGH |
| sindri | 200 "Sindri Python SDK" (Sindri Labs) | 200 Sindri Labs SDK+CLI, 2025-08 | 200 "Rust SDK for the Sindri API", 18k dl | 253; MakerViking/sindricad ★157 | godofwar.fandom; wikipedia; reddit r/GodofWarRagnarok ([brave](https://search.brave.com/search?q=sindri)) | sindri.dev → www.sindri.dev (owned) / sindri.sh 200 (owned) | **HIGH** (Sindri Labs owns all three registries + both domains) |
| goibniu | 404 ([pypi](https://pypi.org/project/goibniu/)) | 404 ([npm](https://registry.npmjs.org/goibniu)) | 404 ([crates](https://crates.io/crates/goibniu)) | **6**; mhooton/goibniu ★2 (empty desc), Masses/Goibniu ★0 (2015) ([search](https://github.com/search?q=goibniu+in%3Aname&type=repositories)) | en.wikipedia.org/wiki/Goibniu; danmachi.fandom; britannica.com "Goibhniu \| Irish God, Smithcraft & Brewing" ([brave](https://search.brave.com/search?q=goibniu)) | NXDOMAIN / NXDOMAIN | **LOW** (only mythology pages; no product anywhere). Spelling is the whole risk: Wikipedia and Britannica themselves use two spellings (Goibniu / Goibhniu) |
| ilmarinen | 404 | 404 | 404 | 28; ilmarinen/mediawiki ★2 | wikipedia; **ilmarinen.fi** (Finnish pension insurer); wikipedia coastal-defence ship ([brave](https://search.brave.com/search?q=ilmarinen)) | NXDOMAIN / NXDOMAIN | MEDIUM (registries clean; page one owned by a large Finnish corporation) |
| hephaestus | 200 empty | 200 headless-chrome tests | 200 2D renderer, 2026-09 | 1,110; Ido-Levi/Hephaestus ★1,187 "Semi-Structured Agentic Framework" | wikipedia; theoi.com; ebsco.com ([brave](https://search.brave.com/search?q=hephaestus)) | — | HIGH (and an agent framework already has it) |
| vulcan | 200 flashcards | 200 logic tool | 200 Pyro pkg manager | 4,359; VulcanJS/Vulcan ★7,893 | — | — | HIGH |
| kaveh | 404 | 200 word censor | 404 | 736 (common Persian given name); top hit is a person | genshin-impact.fandom; wikipedia "Kaveh the Blacksmith"; reddit r/KavehMains ([brave](https://search.brave.com/search?q=kaveh)) | — | HIGH (Genshin Impact character + common first name) |
| regin | 404 | 404 | 404 | 4,745 (substring: Regina, region…); Forairaaaaa/Regina ★130 | regincontrols.com; wikipedia; linkedin regingroup ([brave](https://search.brave.com/search?q=regin)) | — | HIGH on web (regincontrols.com owns page one) |
| mímir / mimir | 200 "Mimir daemons" | 200 bag-of-words | 200 Oracle bindings 17k dl | 2,048; **grafana/mimir ★5,235** | wikipedia; grafana.com/oss/mimir; godofwar.fandom ([brave](https://search.brave.com/search?q=mimir)) | — | **HIGH** |
| smitty | 404 | 200 flux impl | 404 | 422; tkh44/smitty ★207 | youtube "SMii7Y" (YouTuber); wikipedia; vaf.fandom ([brave](https://search.brave.com/search?q=smitty)) | smitty.dev resolves / smitty.sh resolves (both owned, no HTTP) | MEDIUM-HIGH |
| bramble | 200 async logging | 200 npm upgrader | 200 "Bramble protocols" | (not searched — three registries already taken) | — | — | HIGH |
| ornith | 404 | 404 | 404 | 1,009; **ornith-ai/Ornith-1 ★2,054** | **ornith.ai**; ornith.ai blog "Ornith-1.0: Self-Scaffolding LLMs for Agentic Coding"; ollama.com/library/ornith ([brave](https://search.brave.com/search?q=ornith)) | — | **HIGH and disqualified**: Ornith is the compose model this project runs (README T2 stage). Using it would look like the model vendor's product |
| jev | 200 "compiles Python function definitions into Jev (TypeSafe)" | 200 empty | 200 "Client for the TypeSafe System One API", updated 2026-09-17 | 4,365; **browser-use/jev-ultrafast ★5,094** (a dependency of this repo, see pyproject) | typesafe.ai "Introducing System One Models & Jev"; anthonymaio.substack; reddit r/singularity ([brave](https://search.brave.com/search?q=jev)) | — | **HIGH and disqualified**: Jev is TypeSafe's decider model (README T1 stage) and `jev-ultrafast` is a dependency |

### 2c. Two-word compounds

| name | PyPI | npm | crates | GH | Brave top 3 | .dev / .sh | Sev |
|---|---|---|---|---|---|---|---|
| **forgepet** | 404 ([pypi](https://pypi.org/project/forgepet/)) | 404 ([npm](https://registry.npmjs.org/forgepet)) | 404 ([crates](https://crates.io/crates/forgepet)) | **2**; manmaed/ForgePetRock ★1 (2021) ([search](https://github.com/search?q=forgepet+in%3Aname&type=repositories)). `forge-pet` also 404 ×3 | thepetforge.com ("Pet Forge", a pet-supply store); forgepet.com ("Sharing to document personal learning" blog); walmart.com "Pet Forge Spin" pet washer ([brave](https://search.brave.com/search?q=forgepet)) | NXDOMAIN / NXDOMAIN | **LOW** |
| forgemate | 404 | 404 | 404 | 10; GregoryHo/forgemate ★5, sohaildevx/Forgemate ★1 ("Full-stack AI CLI agent", 2026-03), Zenieverse/ForgeMate ★1 ("autonomous developer agent that watches CI", 2026-06) | forgemaster.com; reddit r/castlevania; play.google "Forge Master" ([brave](https://search.brave.com/search?q=forgemate)) — the engine corrects to "forgemaster" | NXDOMAIN / NXDOMAIN | LOW-MEDIUM (two tiny 2026 AI-agent repos already use it; web corrects to Forgemaster) |
| forgebuddy | 404 | 404 | 404 | **0** | **forgebuddy.com "Forge Buddy - Your Laravel Forge Companion Tool"**; medium.com article about it; reddit ([brave](https://search.brave.com/search?q=forgebuddy)) | NXDOMAIN / NXDOMAIN | MEDIUM (registries clean, but the .com is a live dev tool with the same name and the same "forge companion" tagline, for Laravel Forge) |
| smithpet | 404 | 404 | 404 | 3, all false substring matches (`smithpeter`) ([search](https://github.com/search?q=smithpet+in%3Aname&type=repositories)) | oursmithpets.com (pet store); yelp "Smith Pets Store"; wizard101central "Pet:Smith" ([brave](https://search.brave.com/search?q=smithpet)) | NXDOMAIN / NXDOMAIN | LOW (clean, but "Smith" is the most common surname in English and the engine splits it) |
| anvilpet | 404 | 404 | 404 | **0** ([search](https://github.com/search?q=anvilpet+in%3Aname&type=repositories)) | crazy-craft.fandom "Anvil pet"; reddit r/feedthebeast "anvil pet?"; wowhead "Alvin the Anvil" ([brave](https://search.brave.com/search?q=anvilpet)) | NXDOMAIN / NXDOMAIN | **LOW / NONE** (the cleanest string audited; but the character is a smith, not an anvil) |
| deskforge | 404 | 200 "Build everything. Own everything. Pay for nothing." v1.0.9, 2026-07 | 404 | 21; bashrusakh/DeskForge ★7 (remote-desktop server, 2026-09) | thedeskforge.com; **deskforge.app "Desktop apps made easy"**; deskforge.de Remote-Desktop ([brave](https://search.brave.com/search?q=deskforge)) | deskforge.dev resolves HTTP 200 / NXDOMAIN | MEDIUM-HIGH (three live products) |

## 3. Prior art: the "pet for people who run coding agents" category

Search phrase `claude code pet` on GitHub returns 665 repos ([search](https://github.com/search?q=claude+code+pet&type=repositories)).
Top by stars, fetched via `gh api repos/…` on 2026-09-18:

| repo | stars | description (verbatim, truncated) |
|---|---|---|
| [NanmiCoder/cc-haha](https://github.com/NanmiCoder/cc-haha) | 14,624 | "Local-first cross-platform desktop workspace for Claude Code / agents" |
| [pixel-agents-hq/pixel-agents](https://github.com/pixel-agents-hq/pixel-agents) | 9,337 | "Pixel office." |
| [rullerzhou-afk/clawd-on-desk](https://github.com/rullerzhou-afk/clawd-on-desk) | 6,243 | "A pixel desktop pet that watches Claude Code, Codex, Cursor & other AI coding agents — so you don't…" |
| [crafter-station/petdex](https://github.com/crafter-station/petdex) | 4,129 | "A public gallery of animated pets for Codex, Claude Code, DeepSeek Harness, Hermes, OpenCode, Gemini…" |
| [anthropics/claude-desktop-buddy](https://github.com/anthropics/claude-desktop-buddy) | 2,598 | "Reference and an example for the Bluetooth API for makers in Claude Cowork & Claude Code Desktop" |
| [Ido-Levi/claude-code-tamagotchi](https://github.com/Ido-Levi/claude-code-tamagotchi) | 436 | "Real-time behavioral enforcement for Claude Code. Monitors AI actions…" |
| [ntd4996/agentpet](https://github.com/ntd4996/agentpet) | 357 | "A desktop pet for macOS & Windows that monitors your AI coding agents (Claude Code, Codex, Cursor…" |
| [rainnoon/oc-claw](https://github.com/rainnoon/oc-claw) | 351 | "A desktop pet that monitors AI coding agents (OpenClaw, Claude Code, Codex, Cursor…" |
| [QingJ01/Clyde](https://github.com/QingJ01/Clyde) | 142 | "A desktop pet that reacts to your AI coding agent sessions in real-time." |
| [alvinunreal/claude-pets](https://github.com/alvinunreal/claude-pets) | 107 | "Claude Code integration for OpenPets" |
| [itmesneha/agentrocky](https://github.com/itmesneha/agentrocky) | 72 | "macOS desktop companion app that puts an animated pixel-a…" |
| [fredruss/agent-paperclip](https://github.com/fredruss/agent-paperclip) | 31 | "Desktop 'Paperclip' Companion for Claude Code & Codex" |
| [team9ai/bobber](https://github.com/team9ai/bobber) | 23 | "Native macOS floating desktop companion monitoring Claude Code sessions" |

Brave web search `claude code desktop pet` page one: reddit r/ClaudeAI "I built a desktop pet for
Claude Code", github claude-pets, github clawd-on-desk, github anthropics/claude-desktop-buddy,
github IMMINJU/claude-pet ([brave](https://search.brave.com/search?q=claude+code+desktop+pet)).

Two things fall out of this for naming:

1. The words users type are **"desktop pet" / "pet" / "buddy" / "companion" + the agent name**.
   Nobody types "forge". A name containing "pet" is aligned with the query; "companion" is
   already spent by Anthropic's own `claude-desktop-buddy` and a dozen "desktop companion" repos.
2. Every strong competitor is named for the *agent it watches* (clawd, claude-pet, oc-claw,
   codex-pets). This project is harness-agnostic and named for the *character*. That is a real
   differentiator only if the character name is itself findable, which brings it back to
   `forgepet` (category word + character lineage) rather than `forge` (character lineage only).

## 4. How the search surfaces actually match names

- **PyPI normalization.** PEP 503: "The name should be lowercased with all runs of the
  characters `.`, `-`, or `_` replaced with a single `-` character", reference implementation
  `re.sub(r"[-_.]+", "-", name).lower()` ([PEP 503, Normalized Names](https://peps.python.org/pep-0503/#normalized-names)).
  The packaging spec lists `Friendly-Bard`, `friendly.bard`, `friendly_bard` as "all equivalent",
  normalizing to `friendly-bard` ([packaging.python.org name-normalization](https://packaging.python.org/en/latest/specifications/name-normalization/)).
  So `forge-companion` == `forge_companion` == `forge.companion` on PyPI; a 404 on one is a 404 on
  all. `forgepet` and `forge-pet` are **different** names under this rule (no separator run to
  collapse), which is why both were checked; both are 404.
- **crates.io.** "Case-insensitive collision detection" and "Prevent differences of `-` vs `_`"
  are crates.io-imposed limits ([Cargo registry index, Name restrictions](https://doc.rust-lang.org/cargo/reference/registry-index.html#name-restrictions)).
  Same practical effect as PyPI for hyphen/underscore.
- **npm scopes as an escape hatch.** "A scope allows you to create a package with the same name as
  a package created by another user or organization without conflict", format `@scope/package-name`
  ([npm docs, About scopes](https://docs.npmjs.com/about-scopes)). So even for the taken `forge`,
  `@eyalm321/forge` publishes; but scoped names are not what people type into a search box, so
  this solves the registry problem and not the searchability one.
- **GitHub repository search.** The `in:name`, `in:description`, `in:topics`, `in:readme`
  qualifiers restrict which field is matched ([GitHub docs, Searching for repositories](https://docs.github.com/en/search-github/searching-on-github/searching-for-repositories)).
  Ranking: "Unless another sort option is provided as a query parameter, results are sorted by best
  match in descending order. Multiple factors are combined to boost the most relevant item to the
  top of the result list" ([GitHub REST docs, Search](https://docs.github.com/en/rest/search/search)).
  `in:name` is a *substring* match, which is why `forgehand` counts `ForgeHandshake` and `regin`
  counts `Regina`. For a user typing a name into github.com the practical rule is: if a repo with
  tens of thousands of stars contains your word, you are not on page one (see `forge`, 158k repos).
- **Google exact phrase.** "To search for an exact match of a word or phrase: Enter a word or a
  phrase inside quotes", example `"tallest building"` ([Google, Refine web searches](https://support.google.com/websearch/answer/2466433)).
  Unquoted `forge companion` is two independent terms and each is dominated by Minecraft
  Forge / CurseForge / Forge of Empires (Bing and Brave both show this above). Quoted, the phrase
  finds a physical gas forge product. A one-token name (`forgepet`) is an exact phrase by
  construction and needs no quotes.

## 5. Recommendation

Ranked by the single criterion (findable + collision-free), shortest to longest odds:

1. **`forgepet`** — registries 404 ×3 (also `forge-pet`), 2 GitHub hits (both dead), `.dev`/`.sh`
   unregistered, web page one is a blog and a pet-washer. Spellable, category-aligned ("pet"),
   keeps the forge/blacksmith lineage. Console names: `forgepet`, `forgepetd`, `forgepet-mcp`.
2. **`anvilpet`** — the cleanest string of all (0 GitHub, 404 ×3, domains free) but the mascot
   is a smith, not an anvil; only pick this if the name is allowed to drift from the character.
3. **`goibniu`** — the only proper name with zero product collisions anywhere; page one is
   Wikipedia and Britannica. Fails the "hear it, type it" half of the criterion (two spellings, non-
   obvious pronunciation). Good as the *character's* name inside the app even if the package is
   `forgepet`.
4. **`hearthsmith`** — registries clean, but search engines correct it to "heartsmith" (jewelry)
   and hearthsmith.com is a live home-décor store.
5. **`forgemate`** — registries clean, but two 2026 AI-agent repos already use it and the engine
   corrects to "Forgemaster".

Do not use: `forge`, `forge companion`, `forged`, `forge-mcp` (all HIGH); `ornith`, `jev`
(vendor brands of this project's own models); `forgehand` (forgehand.dev is a live AI-code-review
product); `sindri`, `brokkr`, `mimir`, `hephaestus`, `wayland`, `smithy`, `anvil` (major products).
