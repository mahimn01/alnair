# What gets a command-line developer tool starred

Research notes written on 2026-11-20 for Alnair. They cover what the evidence says about stars and adoption for command-line developer tools, with extra attention to Claude Code add-ons and other AI coding tools. The audit of this repository against these notes is in [repo-audit.md](repo-audit.md).

## How to read this

Each statement is one of three kinds.

- **Quoted.** Taken from a paper or an official page. The page was saved and the quoted words were checked against the saved copy. Every quote is listed under [Quoted evidence](#quoted-evidence). A passed check means the words are in the source. It does not mean the source is right, or that the page still says the same thing.
- **Measured.** Counted from 142 public repositories, read through the GitHub API on 2026-11-20. The per-repository numbers are in [data/repos-2026-11-20.csv](data/repos-2026-11-20.csv), and [data/fetch_repos.py](data/fetch_repos.py) is the script that collected them.
- **Judgment.** A reading of the two above. It is marked as judgment each time.

Two limits apply to everything measured. First, the 142 repositories are popular ones, so the counts show what popular projects have in common. They cannot show that a habit caused the stars, because no unpopular projects were measured beside them. Second, the README counts come from pattern matching. An "animated demo" is a GIF, a video or an asciinema link, and an "install command" is a line such as `brew install` or `curl ... | sh`. A few repositories will be counted wrongly in either direction.

## The ten findings that matter most

1. **People star a project to say thanks, to bookmark it, and because they use it** (quoted, Borges and Valente 2018). In that survey 52.5% of developers said they starred a repository to show appreciation. Judgment: a star comes after the visitor has understood the project or got it working, so the first screen and the install deserve the most care.
2. **Stars are a trust signal.** In a survey of 791 developers, three out of four said they consider the number of stars before using or contributing to a project (quoted, Borges and Valente 2018). Judgment: the first stars are the hardest to get, which is the case for a planned launch.
3. **Bought stars backfire.** Fake stars only have a promotion effect for less than two months and become a liability in the long term (quoted, He et al. 2024).
4. **The README is the first thing a visitor sees, and many leave out the why.** GitHub says a README is often the first item a visitor sees. A study of README files found that many lack information about the purpose and status of a repository (quoted, GitHub Docs and Prana et al. 2018).
5. **Popular tools show the product early.** An image appears in the first 40 lines of the README in 19/20 command-line tools and 27/34 AI coding tools. An animated demo appears somewhere in 11/20 command-line tools and 8/34 AI coding tools (measured).
6. **The install command is in the README.** 18/20 command-line tools and 33/34 AI coding tools have one. Among AI coding tools it sits in the first 60 lines in 19/34 (measured).
7. **They publish releases.** 20/20 command-line tools and 31/34 AI coding tools publish GitHub releases. The median count is 82 for the first group and 165.5 for the second (measured).
8. **Hacker News is the one launch channel with numbers behind it.** A study of 138 repository launches of AI tools found an average gain of 121 stars within 24 hours, 189 stars within 48 hours, and 289 stars within a week. The median values run much lower. The 12-17 UTC posting window performed best, and the Show HN tag showed no statistical advantage (quoted, Kraishan 2025).
9. **Badges work when they are backed by something real.** Badges that show build status and test coverage are mostly reliable signals, but the effects do not always persist (quoted, Trockman et al. 2018). The median popular README carries 3 badges near the top among command-line tools and 4 among AI coding tools (measured).
10. **The trust files are common and cheap.** A CONTRIBUTING file: 18/20 and 26/34. A security policy: 13/20 and 21/34. An MIT or Apache-2.0 license: 15/20 and 25/34 (measured, command-line tools first). Judgment: each takes an hour and removes a reason to distrust a new project.

## What 142 popular repositories have

The sample has three parts.

- **20 command-line tools**: uv, fzf, lazygit, ripgrep, bat, starship, ruff, cli, fd, zoxide, just, mise, zellij, delta, atuin, gum, eza, vhs, asciinema, llm.
- **34 AI coding tools and Claude Code add-ons**: superpowers, opencode, claude-code, spec-kit, codex, pi, claude-mem, gemini-cli, OpenHands, ruflo, cline, mem0, context7, goose, BMAD-METHOD, aider, agents, oh-my-claudecode, claude-code-router, continue, claude-code-templates, serena, qwen-code, crush, beads, vibe-kanban, claude-task-master, SuperClaude_Framework, opcode, ccusage, ccstatusline, claude-squad, claude-code-hooks-mastery, Continuous-Claude-v3.
- **88 more** that GitHub repository searches returned when sorted by stars. They are marked `other` in the CSV and only appear in the last column below.

| Measure | 20 command-line tools | 34 AI coding tools | All 142 |
|---|---|---|---|
| Median stars | 38716 | 41839 | 28997.5 |
| Median README length in words | 2264 | 1827.5 | 2224.5 |
| Median README length in lines | 432 | 266 | 324.5 |
| Image in the first 40 lines | 19/20 | 27/34 | 117/142 |
| Animated demo anywhere in the README | 11/20 | 8/34 | 52/142 |
| Animated demo in the first 60 lines | 9/20 | 6/34 | 40/142 |
| Install command in the README | 18/20 | 33/34 | 126/142 |
| Median line of the first install command | 100 | 54 | 67 |
| Install command in the first 60 lines | 4/20 | 19/34 | 59/142 |
| At least one badge in the first 40 lines | 17/20 | 26/34 | 108/142 |
| Median badges in the first 40 lines | 3 | 4 | 4 |
| Custom social preview image | 9/20 | 9/34 | 35/142 |
| Homepage link set | 14/20 | 26/34 | 101/142 |
| Median number of topics | 7.5 | 6 | 10 |
| Discussions turned on | 17/20 | 27/34 | 82/142 |
| Publishes GitHub releases | 20/20 | 31/34 | 111/142 |
| Median number of releases | 82 | 165.5 | 42.5 |
| Changelog file in the root | 14/20 | 18/34 | 69/142 |
| MIT or Apache-2.0 license | 15/20 | 25/34 | 109/142 |
| Security policy | 13/20 | 21/34 | 75/142 |
| CONTRIBUTING file | 18/20 | 26/34 | 103/142 |
| Code of conduct | 9/20 | 11/34 | 43/142 |
| Issue templates | 10/20 | 7/34 | 29/142 |
| Pull request template | 11/20 | 18/34 | 71/142 |
| docs folder | 14/20 | 26/34 | 103/142 |
| examples folder | 7/20 | 9/34 | 34/142 |
| README section: install or quickstart | 16/20 | 30/34 | 123/142 |
| README section: features | 5/20 | 19/34 | 55/142 |
| README section: why | 1/20 | 7/34 | 43/142 |
| README section: comparison | 5/20 | 4/34 | 23/142 |
| README section: FAQ | 4/20 | 4/34 | 24/142 |
| README section: benchmarks | 2/20 | 3/34 | 12/142 |
| README section: how it works | 0/20 | 9/34 | 45/142 |
| README has a table | 8/20 | 25/34 | 106/142 |

Three things stand out (judgment).

- The two groups write different READMEs. Command-line tools run long (median 2264 words) and reach the install command late (median line 100), often after a demo and a feature tour. AI coding tools are shorter (median 1827.5 words) and reach the install command sooner (median line 54).
- Sections that feel standard are less common than expected. A comparison section appears in 5/20 and 4/34, an FAQ in 4/20 and 4/34, and a benchmarks section in 2/20 and 3/34.
- A custom social preview image is a minority habit even here: 9/20 and 9/34.

## Topic by topic

### The first line

Measured, from the repository descriptions on 2026-11-20:

| Repository | Description |
|---|---|
| astral-sh/uv | An extremely fast Python package and project manager, written in Rust. |
| ajeetdsouza/zoxide | A smarter cd command. Supports all major shells. |
| jesseduffield/lazygit | simple terminal UI for git commands |
| Aider-AI/aider | aider is AI pair programming in your terminal |
| anomalyco/opencode | The open source coding agent. |
| simonw/llm | Access large language models from the command-line |
| charmbracelet/vhs | Your CLI home video recorder |

Each one names what the tool is in a dozen words or fewer. Judgment: Alnair needs one sentence of that kind that names the category (a harness for Claude Code and Codex) and the difference (what the reader gets that plain Claude Code does not give).

### README structure

- **What, why, how.** GitHub says a README typically explains why the project is useful and how users can get started. The Open Source Guides add that a README should explain why the project matters and what users can do with it, and that if a project is not yet ready for production, the README should say so (quoted).
- **Why this exists.** Many README files lack the purpose and status of the repository (quoted, Prana et al. 2018). Judgment: a short "the problem" paragraph and an honest status line fill a gap most READMEs leave open.
- **Demo above the fold.** 19/20 command-line tools show an image in the first 40 lines, and 9/20 show an animated demo in the first 60 (measured). The Command Line Interface Guidelines say to lead with examples, because users tend to use examples over other forms of documentation (quoted). VHS exists to "write terminal GIFs as code", so a demo can be re-recorded from a script when the tool changes (quoted, VHS README).
- **Quickstart.** An install or quickstart section appears in 16/20 and 30/34 (measured).
- **Feature list.** 19/34 AI coding tools have a features section, against 5/20 command-line tools (measured).
- **Comparison table.** Uncommon: 5/20 and 4/34 (measured). Judgment: an add-on to a well-known tool is a special case. The visitor already uses Claude Code and wants to know what changes, so one short table earns its place.
- **FAQ.** Uncommon: 4/20 and 4/34 (measured). Judgment: keep it to the questions a stranger asks before installing: what it costs, what it changes on the machine, and how to remove it.
- **How it works.** 9/34 AI coding tools explain their design in the README, and 0/20 command-line tools do (measured). Judgment: a harness with hooks and guards has to earn trust, so one diagram is worth the space.

### Install friction

The install command is in the README for 18/20 and 33/34 (measured). The Show HN guidelines ask that people can try the thing easily, ideally without signups or emails, and say Show HN is for things people can run on their computers (quoted). Judgment: one copy-and-paste command, a plain statement of what it changes on the machine, and a command that proves the install worked are the three things a careful stranger looks for before running a script from the internet.

### Social preview image

Until an image is added, a link to the repository shows basic information and the owner's avatar. GitHub recommends an image of at least 640 by 320 pixels, and 1280 by 640 pixels for best display, as a PNG, JPG or GIF under 1 MB (quoted, GitHub Docs). 9/20 command-line tools and 9/34 AI coding tools have set one (measured). Judgment: it is optional, but a launch post is a pasted link, and the image is what that link looks like.

### Description, topics and website

GitHub says helpful topics name the repository's intended purpose, subject area, community or language, and allows no more than 20 topics (quoted). The median popular repository uses 7.5 topics among command-line tools and 6 among AI coding tools, and 14/20 and 26/34 set a homepage link (measured). Judgment: the description should be the README's first line, and the topics should be the words people type into GitHub search.

### Releases and versioning

20/20 and 31/34 publish GitHub releases (measured). People can be notified of new releases without other repository notifications, and GitHub can generate release notes that list merged pull requests, contributors and a link to a full changelog (quoted, GitHub Docs). Semantic Versioning suggests starting at 0.1.0 and incrementing the minor version for each later release (quoted). Judgment: a release is the only update a user who is not watching the repository will hear about, so work that sits unreleased is invisible.

### Changelog

A changelog is a curated, chronologically ordered list of notable changes for each version, written for humans, with an entry for every version (quoted, Keep a Changelog). A changelog file sits in the root of 14/20 and 18/34 (measured).

### Issue and pull request templates

Issue forms help maintainers receive specific, structured information (quoted, GitHub Docs). Issue templates appear in 10/20 and 7/34, and a pull request template in 11/20 and 18/34 (measured).

### Discussions

An administrator must enable Discussions before anyone can use them (quoted, GitHub Docs). 17/20 and 27/34 have them on (measured). Judgment: for a new project this is where "how do I" questions go, which keeps the issue tracker for bugs.

### CI and badges

17/20 and 26/34 show at least one badge in the first 40 lines (measured). The badge study found that build status, test coverage and dependency badges are mostly reliable signals, and that the effects do not always persist (quoted, Trockman et al. 2018). Judgment: show the badges that are backed by something real, which here means the test workflow and the license.

### Docs site

The Open Source Guides say a website makes a project friendlier and easier to navigate when it is paired with clear documentation (quoted). 14/20 and 26/34 keep a docs folder (measured). Judgment: at Alnair's size a docs folder linked from the README does the job, and a site can wait until there are users asking for one.

### Examples

The Command Line Interface Guidelines say to lead with examples (quoted). An examples folder appears in 7/20 and 9/34 (measured). Judgment: for a terminal tool, commands with their real output in the README do more than a folder most visitors never open.

### Benchmarks

A benchmarks section is rare: 2/20 and 3/34 (measured). Judgment: a number helps only when the reader can reproduce it. Alnair has two benchmark kits in the tree and no published results, so the README should make no performance claim until there are results to link.

### Licensing

MIT, Apache 2.0 and GPLv3 are the most popular open source licenses (quoted, Open Source Guides). 15/20 and 25/34 use MIT or Apache-2.0 (measured).

### Security policy

A security policy should say which versions are supported and how to report a vulnerability (quoted, GitHub Docs). 13/20 and 21/34 have one (measured). Judgment: it matters more than usual for a tool that installs hooks and runs commands on the user's machine.

### Contributor experience

GitHub's community profile checklist looks for README, CODE_OF_CONDUCT, LICENSE and CONTRIBUTING files. The Open Source Guides say to link the CONTRIBUTING file from the README so more people see it (quoted). A CONTRIBUTING file appears in 18/20 and 26/34, and a code of conduct in 9/20 and 11/34 (measured).

### Launch and distribution

- **Hacker News.** The average gains in the launch study were 121 stars within 24 hours, 189 within 48 hours and 289 within a week, with much lower medians. Repositories posted during the best hours gained about 200 more stars than those posted at poor times, and the 12-17 UTC window performed best (quoted, Kraishan 2025). A second study tracked 1,814 AI repositories after they were shared on Hacker News and observed a significant increase in forks, stars and contributors (quoted, Meakpaiboonwattana et al. 2025). An older study of popular projects also shows the importance of Hacker News for promoting open source (quoted, Borges and Valente 2019).
- **Show HN rules.** Make it easy to try, expect that early work is welcome, and do not ask friends to upvote or comment (quoted, Show HN guidelines).
- **Other channels.** Among popular projects, Twitter, user meetings and blogs were the most common promotion channels (quoted, Borges and Valente 2019). The Open Source Guides name Stack Overflow, Reddit, Hacker News and Quora as places to find an audience, advise helping others before asking for things in return, and note that most launches are an iterative process that can take months or years (quoted).
- **Reddit, X and awesome lists.** No primary source with numbers was found for these, so this part is judgment. Post where Claude Code users already talk, lead with the demo, and read the rules of each community and each list before submitting. The awesome-claude-code list was fetched but its submission rules were not read.

## Evidence against the easy story

These were looked for on purpose.

- **The Show HN tag does not help by itself.** It showed no statistical advantage after controlling for other factors, and the median launch gains are much lower than the averages (quoted, Kraishan 2025). A launch post is a chance, with no guaranteed result.
- **Badges are weak evidence.** The effects do not always persist (quoted, Trockman et al. 2018).
- **Several "standard" README sections are rare among the most starred projects**: comparison, FAQ and benchmarks (measured, above). They are included in Alnair's README for the reasons given, and kept short.
- **Some very popular repositories have thin READMEs.** The README of anthropics/claude-code is 400 words, and the description of ccusage/ccusage is `npx ccusage` (measured). Projects with an audience elsewhere do not need the README to do the selling.
- **Star counts depend on things a README cannot change.** Programming language and application domain are among the main factors (quoted, Borges, Hora and Valente 2016).
- **The star surveys are from 2018 and earlier.** Habits may have moved since.

## What could not be checked

- Wang, Wang and Chen, "Study the correlation between the readme file of GitHub projects and their popularity" (Journal of Systems and Software, 2023, doi:10.1016/j.jss.2023.111806), is behind a paywall. Only its title was read, and nothing here relies on it.
- The paper search failed on five of eight queries with a tool error, so the papers above were fetched by identifier. Recent work on this subject may be missing.
- Borges, Hora and Valente 2016, Prana et al. 2018, He et al. 2024 and Borges and Valente 2019 were read as abstracts. Trockman et al. 2018 and Meakpaiboonwattana et al. 2025 were read as the abstracts held by the OpenAlex index. Borges and Valente 2018 and Kraishan 2025 were read in full text extracted from the PDF, where odd spacing is possible.
- Kraishan 2025 is a preprint with one author.

## Quoted evidence

Each row is a statement used above, the exact words it rests on, and the source.

| Statement | Quoted words | Source |
|---|---|---|
| In a survey of 791 developers, three out of four said they consider the number of stars before using or contributing to a project. | "by surveying 791 developers, we report that three out of four developers consider the number of stars before using or contributing to a" | Borges and Valente 2018 |
| More than half of surveyed developers (52.5%) said they starred a repository to show appreciation. | "To show appreciation: More than half of the participants (52.5%) answered they starred the repositories" | Borges and Valente 2018 |
| Developers star repositories for three main reasons: to show appreciation, to bookmark a project, and because they use it. | "frequently overlap): to show appreciation to projects, to bookmark a project, and because they are using a" | Borges and Valente 2018 |
| Star counts vary with programming language and application domain. | "we reveal the main factors that impact the number of stars of GitHub projects, including programming language and application domain" | Borges, Hora and Valente 2016 |
| Across the launches studied, repositories gained an average of 121 stars within 24 hours, 189 stars within 48 hours, and 289 stars within a week of Hacker News exposure. | "repositories gain an average of 121 stars within 24 hours, 189 stars within 48 hours, and 289 stars within a week of HN exposure" | Kraishan 2025 |
| Median gains are much lower than the averages: most launches see moderate growth and a few go viral. | "the median values run much lower, revealing that most launches see moderate growth while a select few go viral" | Kraishan 2025 |
| The 12-17 UTC posting window performed best in that study. | "The 12-17 UTC window performs best, catching both U.S. morning activity and" | Kraishan 2025 |
| Repositories posted during the best hours gained about 200 more stars than those posted at poor times. | "Repositories posted during optimal hours gain about 200 more stars than" | Kraishan 2025 |
| The Show HN tag showed no statistical advantage after controlling for other factors. | "tag shows no statistical advantage after controlling for other factors" | Kraishan 2025 |
| A study of 1,814 AI repositories shared on Hacker News observed a significant increase in forks, stars and contributors afterwards. | "By tracking activity on the associated 1,814 GitHub repositories after they were shared on Hacker News, we observed a significant increase in forks, stars, and contributors." | Meakpaiboonwattana et al. 2025 |
| The same study shows the importance of Hacker News for promoting open source. | "we show the importance of a popular news aggregation site (Hacker News) on the promotion of open source" | Borges and Valente 2019 |
| Fake stars only help in the short term (less than two months) and become a liability in the long term. | "term (i.e., less than two months) and become a liability in the long term" | He et al. 2024 |
| Badges that show build status, test coverage and dependency freshness are mostly reliable signals. | "display the build status, test coverage, and up-to-dateness of dependencies, are mostly reliable signals, correlating with more tests, better pull requests, and fresher dependencies" | Trockman et al. 2018 |
| Displaying such badges correlates with best practices, but the effects do not always persist. | "Displaying such badges correlates with best practices, but the effects do not always persist." | Trockman et al. 2018 |
| Many README files lack information about the purpose and status of a repository. | "while many README files lack information regarding the purpose and status of a repository" | Prana et al. 2018 |
| A README is often the first thing a visitor sees in a repository. | "A README is often the first item a visitor will see when visiting your repository." | GitHub Docs: About READMEs |
| GitHub recommends a social preview image of at least 640 by 320 pixels, and 1280 by 640 pixels for best display. | "we recommend a size of at least 640 by 320 pixels (1280 by 640 pixels for best display)" | GitHub Docs: Social media preview |
| The social preview image should be a PNG, JPG or GIF under 1 MB. | "Your image should be a PNG, JPG, or GIF file under 1 MB in size." | GitHub Docs: Social media preview |
| Until an image is added, repository links show basic information and the owner's avatar. | "Until you add an image, repository links expand to show basic information about the repository and the owner's avatar." | GitHub Docs: Social media preview |
| Helpful topics name the repository's intended purpose, subject area, community or language. | "Helpful topics to classify a repository include the repository's intended purpose, subject area, community, or language." | GitHub Docs: Topics |
| A repository can have no more than 20 topics. | "- Add no more than 20 topics." | GitHub Docs: Topics |
| GitHub's community profile checklist looks for README, CODE_OF_CONDUCT, LICENSE and CONTRIBUTING files. | "The community profile checklist checks to see if a project includes recommended community health files, such as README, CODE_OF_CONDUCT, LICENSE, or CONTRIBUTING, in a supported location." | GitHub Docs: Community profiles |
| Discussions must be enabled by an administrator before anyone can use them. | "an administrator must enable GitHub Discussions for the repository or organization first" | GitHub Docs: About Discussions |
| People can get notifications for new releases without other repository notifications. | "You can receive notifications when new releases are published in a repository without receiving notifications about other updates to the repository." | GitHub Docs: About releases |
| Automatically generated release notes list merged pull requests, contributors and a link to a full changelog. | "Automatically generated release notes include a list of merged pull requests, a list of contributors to the release, and a link to a full changelog." | GitHub Docs: Generated release notes |
| Issue forms help maintainers receive specific, structured information. | "issue forms help ensure that you receive your desired information" | GitHub Docs: Issue and pull request templates |
| Before promoting a project you should be able to explain what it does and why it matters. | "Before you start the actual work of promoting your project, you should be able to explain what it does, and why it matters." | Open Source Guides: Finding users |
| Most project launches are an iterative process that can take months or years. | "Most project launches are an iterative process that can take months or years." | Open Source Guides: Finding users |
| A README should also say when a project is not ready for production or does not accept contributions. | "or your project is not yet ready for production, write this information down" | Open Source Guides: Starting a project |
| Show HN asks that people can try the thing easily, ideally without signups or emails. | "Please make it easy for users to try your thing out, ideally without barriers such as signups or emails." | Show HN guidelines |
| Show HN rules say not to ask friends to upvote or comment. | "Please don't ask friends to upvote or comment." | Show HN guidelines |
| Keep a Changelog says changelogs are for humans and every version should have an entry. | "- There should be an entry for every single version." | Keep a Changelog 1.1.0 |
| Semantic Versioning suggests starting at 0.1.0 and incrementing the minor version for each later release. | "The simplest thing to do is start your initial development release at 0.1.0 and then increment the minor version for each subsequent release." | Semantic Versioning 2.0.0 |
| The Command Line Interface Guidelines say to lead with examples because users prefer them to other documentation. | "Lead with examples. Users tend to use examples over other forms of documentation, so show them first in the help page" | Command Line Interface Guidelines |
| Among popular projects studied, Twitter, user meetings and blogs were the most common promotion channels. | "meetings, and blogs are the most common promotion channels used by the studied projects" | Borges and Valente 2019 |
| A security policy should say which versions are supported and how to report a vulnerability. | "add information about supported versions of your project and how to report a vulnerability" | GitHub Docs: Security policy |
| VHS lets you write terminal GIFs as code for demoing CLI tools. | "Write terminal GIFs as code for integration testing and demoing your CLI tools." | VHS README |
| A project website makes a project friendlier and easier to navigate when paired with clear documentation. | "A website makes your project friendlier and easier to navigate, especially when it’s paired with clear documentation and tutorials." | Open Source Guides: Finding users |
| Show HN accepts work at an early stage and does not require polish. | "A Show HN needn't be complicated or look slick. The community is comfortable with work that's at an early stage." | Show HN guidelines |
| GitHub says a README typically explains why the project is useful. | "- Why the project is useful" | GitHub Docs: About READMEs |
| GitHub says a README typically explains how users can get started. | "- How users can get started with the project" | GitHub Docs: About READMEs |
| The Open Source Guides say a README should explain why the project matters and what users can do with it. | "They also explain why your project matters, and what your users can do with it." | Open Source Guides: Starting a project |
| MIT, Apache 2.0 and GPLv3 are the most popular open source licenses. | "MIT, Apache 2.0, and GPLv3 are the most popular open source licenses, but there are other options to choose from." | Open Source Guides: Starting a project |
| The Open Source Guides say to link the CONTRIBUTING file from the README so more people see it. | "Link to your CONTRIBUTING file from your README, so more people see it." | Open Source Guides: Starting a project |
| The launch study analysed 138 repository launches. | "our pipeline analyzes 138 repository launches from" | Kraishan 2025 |
| Show HN is for things people can run on their computers. | "On topic: things people can run on their computers or hold in their hands." | Show HN guidelines |
| The Open Source Guides suggest Stack Overflow, Reddit, Hacker News or Quora as places to find an audience for a software project. | "you can probably find your audience on Stack Overflow, Reddit, Hacker News, or Quora" | Open Source Guides: Finding users |
| The Open Source Guides advise helping others before asking for things in return. | "Generally speaking, focus on helping others before asking for things in return." | Open Source Guides: Finding users |
| A changelog is a curated, chronologically ordered list of notable changes for each version. | "A changelog is a file which contains a curated, chronologically ordered list of notable changes for each version of a project." | Keep a Changelog 1.1.0 |

## Sources

All retrieved on 2026-11-20.

- **Borges, Hora and Valente 2016.** Hudson Borges, Andre Hora, Marco Tulio Valente. "Understanding the Factors that Impact the Popularity of GitHub Repositories." ICSME 2016. https://arxiv.org/abs/1606.04984
- **Borges and Valente 2018.** Hudson Borges, Marco Tulio Valente. "What's in a GitHub Star? Understanding Repository Starring Practices in a Social Coding Platform." Journal of Systems and Software, 2018. https://arxiv.org/abs/1811.07643
- **Borges and Valente 2019.** Hudson Borges, Marco Tulio Valente. "How do Developers Promote Open Source Projects?" IEEE Computer 52(8), 2019. https://arxiv.org/abs/1908.04219
- **He et al. 2024.** Hao He, Haoqin Yang, Philipp Burckhardt, Alexandros Kapravelos, Bogdan Vasilescu, Christian Kästner. "Six Million (Suspected) Fake Stars in GitHub: A Growing Spiral of Popularity Contests, Spams, and Malware." 2024. https://arxiv.org/abs/2412.13459
- **Kraishan 2025.** Obada Kraishan. "Launch-Day Diffusion: Tracking Hacker News Impact on GitHub Stars for AI Tools." 2025. https://arxiv.org/abs/2511.04453
- **Meakpaiboonwattana et al. 2025.** Prachnachai Meakpaiboonwattana and others. "Social Media Reactions to Open Source Promotions: AI-Powered GitHub Projects on Hacker News." ICSME 2025. https://doi.org/10.1109/icsme64153.2025.00039
- **Prana et al. 2018.** Gede Artha Azriadi Prana, Christoph Treude, Ferdian Thung, Thushari Atapattu, David Lo. "Categorizing the Content of GitHub README Files." 2018. https://arxiv.org/abs/1802.06997
- **Trockman et al. 2018.** Asher Trockman, Shurui Zhou, Christian Kästner, Bogdan Vasilescu. "Adding sparkle to social coding: an empirical study of repository badges in the npm ecosystem." ICSE 2018. https://doi.org/10.1145/3180155.3180209
- **GitHub Docs: About READMEs.** https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-readmes
- **GitHub Docs: Social media preview.** https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/customizing-your-repositorys-social-media-preview
- **GitHub Docs: Topics.** https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/classifying-your-repository-with-topics
- **GitHub Docs: Community profiles.** https://docs.github.com/en/communities/setting-up-your-project-for-healthy-contributions/about-community-profiles-for-public-repositories
- **GitHub Docs: About Discussions.** https://docs.github.com/en/discussions/collaborating-with-your-community-using-discussions/about-discussions
- **GitHub Docs: About releases.** https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases
- **GitHub Docs: Generated release notes.** https://docs.github.com/en/repositories/releasing-projects-on-github/automatically-generated-release-notes
- **GitHub Docs: Issue and pull request templates.** https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/about-issue-and-pull-request-templates
- **Open Source Guides: Finding users.** https://opensource.guide/finding-users/
- **Open Source Guides: Starting a project.** https://opensource.guide/starting-a-project/
- **Show HN guidelines.** https://news.ycombinator.com/showhn.html
- **Keep a Changelog 1.1.0.** https://keepachangelog.com/en/1.1.0/
- **Semantic Versioning 2.0.0.** https://semver.org/
- **Command Line Interface Guidelines.** https://clig.dev/
- **GitHub Docs: Security policy.** https://docs.github.com/en/code-security/how-tos/report-and-fix-vulnerabilities/configure-vulnerability-reporting/add-security-policy
- **VHS README.** https://github.com/charmbracelet/vhs/blob/24fa2254a9806091e6ee6a980e9f3bcfe0a9ba53/README.md
