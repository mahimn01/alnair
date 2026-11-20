# Security

Please don't open a public issue for a security problem. Report it privately through GitHub instead: go to the Security tab of this repository and choose "Report a vulnerability". I'll reply within a week.

Things I especially want to hear about:

- any way a session, hook or tool writes to `~/.claude`, `~/.claude.json` or an existing note it didn't create
- a credential reaching a log, a session note, the CLP store or a subprocess argument
- fetched web content being able to steer the research tools or the session
- the verify gate or the research report gate passing a quote that isn't in the cited source

## Supported versions

Fixes go to `main` and into the next release. Older releases don't get patches, so update first (`alnair update`) and check that the problem is still there.
