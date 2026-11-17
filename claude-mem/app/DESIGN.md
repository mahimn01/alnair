# Alnair Mac launcher design

Approved brief: keep directories, ~/.clp formats and all old commands intact; brand the harness Alnair; install ~/Applications/Alnair.app; never launch a model during verification.

The app launches the installed Rune GUI directly with a generated, separate personal profile and starts in ~. It keeps Rune's stock colorful theme and existing CLP helpers. Rune has no advertised startup-command CLI flag, so a narrowly scoped AppleScript sends the existing Control-Command-C layout shortcut only to the newly spawned Rune PID, only with a standard window and no modal sheets. It never clicks controls or answers dialogs. If Automation/Accessibility/trust prevents this, stop bootstrap and leave Rune open; the user can grant access and press the shortcut manually. No AppleScript is executed by --dry-run.

Compared with silently scripting permission dialogs (forbidden) or an unverified custom Rune SDK extension, this reuses the existing, tested GUI layout contract. Rebuild is `alnair install-app`; source and deterministic star icon drawing live here, with a standard-library-only installer and sips/iconutil. Every rebuild preserves the previous bundle/profile in a new timestamped backup. App launch makes no installation changes. Alnair's data and Claude's config are untouched.

Tests mock process starts and validate plist, profile, dry-run argv, PNG signature, and the bootstrap safety guards. Bundle checks are plutil, ad-hoc codesign and codesign --verify; real GUI/Claude testing is left to Mahimn.
