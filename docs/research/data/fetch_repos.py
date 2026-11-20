"""Collect the numbers behind docs/research/oss-best-practices.md.

What it does: asks the GitHub API (through the gh command, which must be logged in) for public facts
about a list of repositories, downloads each README, and counts things in it: where the first image,
animated demo and install command are, how many badges sit near the top, and which sections exist.

Why: so the "measured" numbers in the research notes can be checked again or refreshed.

Run:   python3 fetch_repos.py OUTDIR [owner/repo ...]

It writes only inside OUTDIR: repos.json (everything), repos.csv (one line per repository) and
readmes/ (the README files). A repository already in OUTDIR/repos.json is not fetched again, so a
second run only rewrites repos.csv. It changes nothing on GitHub.
"""
import csv, json, re, subprocess, sys, time
from pathlib import Path

# The three groups the research notes compare. Names are the ones GitHub reported on 2026-11-20.
CLI_TOOLS = """astral-sh/uv junegunn/fzf jesseduffield/lazygit BurntSushi/ripgrep sharkdp/bat starship/starship
astral-sh/ruff cli/cli sharkdp/fd ajeetdsouza/zoxide casey/just jdx/mise zellij-org/zellij dandavison/delta
atuinsh/atuin charmbracelet/gum eza-community/eza charmbracelet/vhs asciinema/asciinema simonw/llm""".split()
AI_TOOLS = """obra/superpowers anomalyco/opencode anthropics/claude-code github/spec-kit openai/codex earendil-works/pi
thedotmack/claude-mem google-gemini/gemini-cli OpenHands/OpenHands ruvnet/ruflo cline/cline mem0ai/mem0
upstash/context7 aaif-goose/goose bmad-code-org/BMAD-METHOD Aider-AI/aider wshobson/agents
Yeachan-Heo/oh-my-claudecode musistudio/claude-code-router continuedev/continue
davila7/claude-code-templates oraios/serena QwenLM/qwen-code charmbracelet/crush gastownhall/beads
BloopAI/vibe-kanban eyaltoledano/claude-task-master SuperClaude-Org/SuperClaude_Framework winfunc/opcode
ccusage/ccusage sirmalloc/ccstatusline smtg-ai/claude-squad disler/claude-code-hooks-mastery
parcadei/Continuous-Claude-v3""".split()
OTHERS = """morluto/rea affaan-m/ECC multica-ai/andrej-karpathy-skills anthropics/skills
x1xhlol/system-prompts-and-models-of-ai-tools garrytan/gstack headroomlabs-ai/headroom
ComposioHQ/awesome-claude-skills colbymchenry/codegraph ai-job-search-dev/ai-job-search
asgeirtj/system_prompts_leaks Alishahryar1/free-claude-code blader/humanizer mvanhorn/last30days-skill
shanraisshan/claude-code-best-practice openinterpreter/openinterpreter cathrynlavery/diagram-design
pingdotgg/t3code gsd-build/get-shit-done ayghri/i-have-adhd coreyhaines31/marketingskills
router-for-me/CLIProxyAPI bojieli/ai-agent-book hesreallyhim/awesome-claude-code Nutlope/hallmark
K-Dense-AI/scientific-agent-skills Imbad0202/academic-research-skills sickn33/agentic-awesome-skills
codewhale-hq/Codewhale can1357/oh-my-pi esengine/DeepSeek-Reasonix alirezarezvani/claude-skills
VoltAgent/awesome-claude-code-subagents 1jehuang/jcode google/elephant-goldfish-model lemomo-ai/lemo-opuscar
kyegomez/OpenMythos coleam00/context-engineering-intro tigerless-labs/autoharness
rehan-remade/universal-modder miqdadbadjuber/anti-slop revfactory/harness patch-gg/pixelfx Q00/ouroboros
nateherkai/scroll-craft haydenschmitty/floorplan Manavarya09/design-extract zenbu-labs/terminal-browser
nyldn/claude-octopus nykooi1/vibe-wise nowork-studio/notfair-plugin JetBrains/go-modern-guidelines
tutti-os/tutti graykode/abtop agenticnotetaking/arscontexta rohitg00/pro-workflow zilliztech/memsearch
Gentleman-Programming/gentle-shell wondelai/skills amElnagdy/delegate-skills WhiteTowerAI/ohmygame
tractorjuice/arc-kit cth9191/animate kenryu42/cc-safety-net composio-community/awesome-claude-plugins
OpenOSINT/OpenOSINT daveebbelaar/bama proxysoul/Empryo repowise-dev/claude-code-prompts ayuayue/PiDeck
sno-ai/sno-station jmfederico/pi-web sno-ai/sno-station-skills dongshuyan/compass-skills
wecode-ai/RunVSAgent Muvon/octomind saltbo/agent-kanban koki7o/claude-code-for-beginners didi/helixharness
JohnCrickett/ai-assisted-engineering syahiidkamil/Software-Engineer-AI-Agent-Atlas timwuhaotian/the-pair
localixai/localix-cli rrezartprebreza/spring-boot-skills get-vix/vix orbi-build/orbi morganlinton/Albatross
AlysisAi/AlysisCode""".split()

def gh(*args: str) -> tuple[int, str]:
    p = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8")
    return p.returncode, p.stdout if p.returncode == 0 else p.stderr

# What counts as a badge, an image, an install command and a named section in a README.
BADGE = re.compile(r"shields\.io|badge\.svg|badgen\.net|/badge/|codecov\.io|badge\.fury|img\.shields|actions/workflows/.+badge|deepwiki|trendshift|api\.star-history|contrib\.rocks|hits\.|visitor|awesome\.re|opencollective\.com/.+/(badge|tiers)|discord\.com/api/guilds|flat\.badgen|snyk\.io|goreportcard|pkg\.go\.dev/badge|repology\.org/badge|packagephobia|deps\.rs|crates\.io/crates/.+\.svg|pepy\.tech|scorecard\.dev|bestpractices", re.I)
IMG = re.compile(r"!\[[^\]]*\]\(([^)\s]+)[^)]*\)|<img[^>]+src=[\"']([^\"']+)[\"']|<video[^>]*src=[\"']([^\"']+)[\"']|<source[^>]+src=[\"']([^\"']+)[\"']", re.I)
BAREVID = re.compile(r"^\s*(https://github\.com/[^\s]+/assets/[^\s]+|https://user-images\.githubusercontent\.com/[^\s]+\.(?:mp4|mov|webm)|https://github\.com/user-attachments/assets/[^\s]+)\s*$")
INSTALL = re.compile(r"\b(brew install|cargo install|cargo binstall|pip3? install|pipx install|uv tool install|uvx |uv pip install|npm i(?:nstall)? (?:-g|--global)|npm install|npx |pnpm (?:add|dlx)|bunx? |go install|winget install|scoop install|choco install|apt(?:-get)? install|pacman -S|nix(?:-env| profile| run| shell)|curl [^\n]*\|\s*(?:sudo )?(?:ba|z)?sh|wget [^\n]*\|\s*(?:ba)?sh|irm [^\n]*\|\s*iex|iwr [^\n]*\|\s*iex|/plugin (?:marketplace add|install)|claude (?:mcp add|plugin)|docker (?:run|pull)|git clone)", re.I)
SECTIONS = {
    "install": r"install|getting started|quick ?start|setup|get started|usage",
    "features": r"feature|what it does|highlights|what you get|capabilit",
    "why": r"\bwhy\b|motivation|philosophy|background|problem|the idea|rationale",
    "compare": r"compar|alternativ|\bvs\b|versus|how it differs|similar (tools|projects)|prior art",
    "faq": r"\bfaq\b|frequently asked|troubleshoot|common (questions|issues)",
    "contributing": r"contribut|development|hacking|building from source",
    "license": r"licen[sc]e",
    "docs": r"\bdocs?\b|documentation|learn more|reference|guide",
    "bench": r"benchmark|performance|speed|how fast",
    "roadmap": r"roadmap|todo|planned|status",
    "community": r"community|discord|support|sponsor|backers|acknowledg|credits|thanks",
    "demo": r"demo|screenshot|in action|showcase|example|tour|preview",
    "how": r"how it works|architecture|design|under the hood|internals",
}

def analyze(md: str) -> dict:
    """Count the README features the notes report. Line numbers start at 1."""
    lines = md.splitlines()
    words = len(re.findall(r"\w+", md))
    first_img = first_anim = first_code = first_install = None
    img_kind = None
    badges_top = 0
    in_code = False
    # Walk the file once: note the first code block, install command, image and animated demo,
    # and count badges in the first 40 lines. Images inside code blocks are ignored.
    for i, line in enumerate(lines, 1):
        if line.strip().startswith("```"):
            if not in_code and first_code is None: first_code = i
            in_code = not in_code
        if first_install is None and INSTALL.search(line): first_install = i
        if in_code: continue
        urls = [u for m in IMG.finditer(line) for u in m.groups() if u]
        if BAREVID.match(line): urls.append(line.strip())
        for u in urls:
            if BADGE.search(u) or BADGE.search(line) and "badge" in line.lower() and not re.search(r"\.(gif|mp4|webm|mov)", u, re.I):
                if i <= 40: badges_top += 1
                continue
            animated = bool(re.search(r"\.(gif|mp4|webm|mov|apng)(\?|$|#)|asciinema\.org|vhs\.charm\.sh|user-attachments/assets|/assets/\d+/|user-images\.githubusercontent\.com/.+\.(gif|mp4)|demo\.(svg|webp)|\.cast", u, re.I))
            if first_img is None: first_img, img_kind = i, ("animated" if animated else ("logo-or-banner" if re.search(r"logo|banner|icon|header|hero|wordmark|brand", u, re.I) else "static"))
            if animated and first_anim is None: first_anim = i
    # Headings, with markdown and HTML stripped, to see which sections exist.
    heads = [(len(m.group(1)), re.sub(r"[#*_\x60]|<[^>]+>|\[([^\]]*)\]\([^)]*\)", r"\1", m.group(2)).strip()) for m in (re.match(r"^(#{1,3})\s+(.*)$", l) for l in lines) if m]
    h2 = [t for lvl, t in heads if lvl == 2]
    found = {k: next((t for lvl, t in heads if lvl >= 2 and re.search(p, t, re.I)), None) for k, p in SECTIONS.items()}
    # the hero: first line of prose (not a heading, badge, image, html tag or link bar)
    hero = None
    for l in lines[:80]:
        s = l.strip()
        if not s or s.startswith(("#", "!", "[!", "<", "|", "-", "*", ">", "[![", "```")) and not re.match(r"^\*\*[A-Za-z]", s): continue
        if BADGE.search(s) or len(re.findall(r"\w+", s)) < 4: continue
        hero = re.sub(r"\s+", " ", s)[:240]; break
    if hero is None:
        m = re.search(r"<(?:p|h[1-6]|strong|b|em)[^>]*>\s*([^<]{25,240})<", "\n".join(lines[:60]))
        hero = re.sub(r"\s+", " ", m.group(1)).strip() if m else None
    return {"lines": len(lines), "words": words, "first_image_line": first_img, "first_image_kind": img_kind, "first_animated_line": first_anim,
            "first_code_line": first_code, "first_install_line": first_install, "badges_in_first_40_lines": badges_top,
            "h2_count": len(h2), "h2": h2[:30], "sections": {k: bool(v) for k, v in found.items()}, "has_table": bool(re.search(r"^\|.+\|\s*$\n^\|[\s:|-]+\|\s*$", md, re.M)) or "<table" in md.lower(),
            "has_details": "<details" in md.lower(), "hero": hero, "hero_words": len(re.findall(r"\w+", hero or "")),
            "centered_header": bool(re.search(r"<(p|div|h1)[^>]+align=[\"']center", "\n".join(lines[:30]), re.I)),
            "translations": bool(re.search(r"README[._-](zh|ja|ko|es|fr|de|pt|ru)|简体中文|日本語|한국어", md[:4000]))}

def graphql_meta(repos: list[str]) -> dict:
    """Ask GitHub for each repository's public settings and counts, six repositories per request."""
    out = {}
    for start in range(0, len(repos), 6):
        chunk = repos[start:start + 6]
        parts = []
        for i, full in enumerate(chunk):
            o, n = full.split("/")
            parts.append(f'r{i}: repository(owner: "{o}", name: "{n}") {{ nameWithOwner stargazerCount forkCount description homepageUrl usesCustomOpenGraphImage hasDiscussionsEnabled hasWikiEnabled isArchived createdAt pushedAt primaryLanguage {{ name }} licenseInfo {{ spdxId }} repositoryTopics(first: 20) {{ nodes {{ topic {{ name }} }} }} releases {{ totalCount }} latestRelease {{ tagName publishedAt }} securityPolicyUrl isSecurityPolicyEnabled codeOfConduct {{ name }} contributingGuidelines {{ url }} issueTemplates {{ name }} pullRequestTemplates {{ filename }} fundingLinks {{ platform }} discussions {{ totalCount }} watchers {{ totalCount }} }}')
        _, res = gh("api", "graphql", "-f", "query=query { " + " ".join(parts) + " }")
        try: data = json.loads(res)["data"]
        except Exception:
            # a missing repo makes gh exit 1 but still print the partial data on stdout
            p = subprocess.run(["gh", "api", "graphql", "-f", "query=query { " + " ".join(parts) + " }"], capture_output=True, text=True)
            try: data = json.loads(p.stdout)["data"]
            except Exception: print("graphql failed", p.stderr[:300], file=sys.stderr); data = {}
        for i, full in enumerate(chunk):
            node = (data or {}).get(f"r{i}")
            if node: out[full] = node
            else: print("missing", full, file=sys.stderr)
    return out

CSV_COLUMNS = ["repo", "group", "stars", "license", "topics", "releases", "homepage_set", "custom_social_image", "discussions",
               "has_changelog", "security_policy", "contributing", "code_of_conduct", "issue_templates", "pr_templates",
               "has_docs_dir", "has_examples_dir", "readme_lines", "readme_words", "first_image_line", "first_image_kind",
               "first_animated_line", "first_install_line", "badges_in_first_40_lines", "has_table", "sec_install", "sec_features",
               "sec_why", "sec_compare", "sec_faq", "sec_bench", "sec_how"]

def csv_row(r: dict) -> list:
    """One line of repos.csv: the settings GitHub reported, then the README counts."""
    md = r["readme"] or {}
    sec = md.get("sections", {})
    group = "cli" if r["repo"] in CLI_TOOLS else "ai-coding" if r["repo"] in AI_TOOLS else "other"
    return [r["repo"], group, r["stars"], r["license"] or "", len(r["topics"]), r["releases"], bool(r["homepage"]), r["custom_social_image"],
            r["discussions"], r["has_changelog"], r["security_policy"], r["contributing"], r["code_of_conduct"], r["issue_templates"],
            r["pr_templates"], r["has_docs_dir"], r["has_examples_dir"], md.get("lines", ""), md.get("words", ""),
            md.get("first_image_line") or "", md.get("first_image_kind") or "", md.get("first_animated_line") or "",
            md.get("first_install_line") or "", md.get("badges_in_first_40_lines", ""), md.get("has_table", ""),
            *[sec.get(k, "") for k in ("install", "features", "why", "compare", "faq", "bench", "how")]]

def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("usage: python3 fetch_repos.py OUTDIR [owner/repo ...]")
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    # Keep what an earlier run saved, one row per repository, and fetch only what is missing.
    saved = out / "repos.json"
    old = json.loads(saved.read_text(encoding="utf-8")) if saved.exists() else []
    rows = list({r["repo"]: r for r in old}.values())
    have = {r["repo"] for r in rows} | {r["asked_as"] for r in rows}
    repos = [r for r in dict.fromkeys(CLI_TOOLS + AI_TOOLS + OTHERS + sys.argv[2:]) if r not in have]
    print("fetching", len(repos), "missing repos")
    meta = graphql_meta(repos)
    still = [r for r in repos if r not in meta]
    if still:
        # One retry: GitHub sometimes answers a batch with a 502.
        time.sleep(5)
        meta.update(graphql_meta(still))
    for full, node in meta.items():
        real = node["nameWithOwner"]
        # The README as plain text, and the names in the top folder (for CHANGELOG, docs, examples).
        code, readme = gh("api", f"repos/{real}/readme", "-H", "Accept: application/vnd.github.raw")
        code2, root = gh("api", f"repos/{real}/contents/")
        names = [e["name"] for e in json.loads(root)] if code2 == 0 else []
        row = {"repo": real, "asked_as": full, "stars": node["stargazerCount"], "forks": node["forkCount"], "description": node["description"], "homepage": node["homepageUrl"],
               "custom_social_image": node["usesCustomOpenGraphImage"], "discussions": node["hasDiscussionsEnabled"], "discussion_count": node["discussions"]["totalCount"],
               "wiki": node["hasWikiEnabled"], "archived": node["isArchived"], "created": node["createdAt"][:10], "pushed": node["pushedAt"][:10],
               "language": (node["primaryLanguage"] or {}).get("name"), "license": (node["licenseInfo"] or {}).get("spdxId"),
               "topics": [t["topic"]["name"] for t in node["repositoryTopics"]["nodes"]], "releases": node["releases"]["totalCount"],
               "latest_release": node["latestRelease"], "security_policy": node["isSecurityPolicyEnabled"], "code_of_conduct": bool(node["codeOfConduct"]),
               "contributing": bool(node["contributingGuidelines"]), "issue_templates": len(node["issueTemplates"] or []), "pr_templates": len(node["pullRequestTemplates"] or []),
               "funding": [f["platform"] for f in node["fundingLinks"]], "root_files": names,
               "has_changelog": any(re.match(r"(changelog|changes|history|releases?)(\.md|\.rst|\.txt)?$", n, re.I) for n in names),
               "has_docs_dir": any(n.lower() in ("docs", "doc", "website", "site", "book") for n in names), "has_examples_dir": any(n.lower() in ("examples", "example", "demos", "demo", "samples") for n in names)}
        row["readme"] = analyze(readme) if code == 0 else None
        if code == 0:
            (out / "readmes").mkdir(exist_ok=True)
            (out / "readmes" / (real.replace("/", "__") + ".md")).write_text(readme, encoding="utf-8")
        rows.append(row)
    rows.sort(key=lambda r: -r["stars"])
    saved.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    with open(out / "repos.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(CSV_COLUMNS)
        w.writerows(csv_row(r) for r in rows)
    print(len(rows), "repos saved;", sum(1 for r in rows if r["readme"]), "with README")

if __name__ == "__main__":
    main()
