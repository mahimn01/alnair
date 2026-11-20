// Windows forms for the lab guards (lab/plugin/hooks/register.js and windows-shell.js), run with plain Node on
// any system:  node --test lab/tests/node/windows-guards.test.mjs
// Each command is written as its tool receives it: Bash text as Git Bash reads it (a backslash stays only inside
// quotes), PowerShell text as PowerShell reads it (a backslash is a path separator, the backtick is the escape).
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {isProtectedPath, nestedSessionDenial, settingsWriteDenial, powershellDenial, powershellSettingsDenial, scriptDenial,
  within} from '../../plugin/hooks/register.js';
import {splitPowerShell, windowsPath, hostCommand, startProcessWords} from '../../plugin/hooks/windows-shell.js';

const BT = '\x60';
// The home folder as Windows Node, PowerShell and Git Bash each give it.
const WIN_HOMES = ['C:\\Users\\yatin', 'C:/Users/yatin', '/c/Users/yatin'];
const HOME = 'C:\\Users\\yatin';
const encoded = text => Buffer.from(text, 'utf16le').toString('base64');
const each = (list, check) => { for (const item of list) check(item); };

test('every Windows spelling of the settings files is protected, however Windows gives the home folder', () => {
  const settings = ['C:\\Users\\yatin\\.claude\\settings.json', 'C:/Users/yatin/.claude/settings.json', '/c/Users/yatin/.claude/settings.json',
    'c:\\users\\YATIN\\.CLAUDE\\Settings.json', 'C:\\Users\\yatin\\.claude.json', '~\\.claude.json', '~/.claude\\settings.json',
    '%USERPROFILE%\\.claude.json', '%userprofile%\\.claude\\settings.json', '$env:USERPROFILE\\.claude.json', '${env:USERPROFILE}\\.claude\\settings.json',
    '$HOME\\.claude.json', '%HOMEDRIVE%%HOMEPATH%\\.claude.json', '\\\\?\\C:\\Users\\yatin\\.claude.json',
    'Microsoft.PowerShell.Core\\FileSystem::C:\\Users\\yatin\\.claude.json', 'C:\\Users\\yatin\\.claude\\..\\.claude.json', '/cygdrive/c/Users/yatin/.claude.json'];
  const others = ['C:\\Users\\yatin\\.claude\\settings.local.json', 'C:\\Users\\other\\.claude.json', 'D:\\Users\\yatin\\.claude.json',
    'C:\\Users\\yatin\\notes\\.claude.json'];
  for (const home of WIN_HOMES) {
    each(settings, path => assert.ok(isProtectedPath(path, home), home + ' ' + path));
    each(others, path => assert.ok(!isProtectedPath(path, home), home + ' ' + path));
  }
  // A relative path is taken from the session's folder, which Windows gives as C:\...
  assert.ok(isProtectedPath('.claude\\settings.json', HOME, 'C:\\Users\\yatin'));
  assert.ok(isProtectedPath('..\\.claude.json', HOME, 'C:\\Users\\yatin\\proj'));
});

test('macOS and Linux paths and folders are read as before', () => {
  assert.ok(isProtectedPath('~/.claude.json', '/Users/u'));
  assert.ok(isProtectedPath('/Users/u/.claude/settings.json', '/Users/u'));
  assert.ok(!isProtectedPath('/Users/u/notes/.claude.json', '/Users/u'));
  assert.ok(!isProtectedPath('~/.claude/settings.local.json', '/Users/u'));
  assert.ok(within('/Users/u/run/x', '/Users/u/run'));
  assert.ok(!within('/Users/u/runner/x', '/Users/u/run'));
  assert.ok(!within('/Users/u/Run/x', '/Users/u/run'));
});

test('folders Windows gives match with either slash and in any case', () => {
  assert.ok(within('C:\\Users\\yatin\\proj\\a.py', 'C:/Users/yatin/proj'));
  assert.ok(within('c:/users/yatin/PROJ/a.py', 'C:\\Users\\yatin\\proj'));
  assert.ok(!within('C:\\Users\\yatin\\project\\a.py', 'C:/Users/yatin/proj'));
});

test('Bash (Git Bash) commands that start a session through a Windows name, PowerShell or cmd are refused', () => {
  each(['claude.exe -p hi', 'CLAUDE -p hi', 'Claude.EXE --print hi', "'C:\\Users\\yatin\\.local\\bin\\claude.exe' -p hi",
    '/c/Users/yatin/.local/bin/claude -p hi', 'alnair.cmd -p hi', 'codex.exe exec hi', "pwsh -c 'claude -p x'",
    "pwsh -NoProfile -Command '& claude -p x'", 'powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "claude -p x"',
    "pwsh /command 'claude -p x'", 'pwsh -EncodedCommand ' + encoded('claude -p x'), 'powershell -enc ' + encoded('& claude.exe -p x'),
    'pwsh -e not*base64', 'cmd /c claude -p', 'cmd.exe /c "claude -p x"', 'cmd //c claude -p', 'cmd /C cl^aude -p',
    'cmd /k "cd x & claude -p"', 'wsl claude -p', "pwsh -c 'Start-Process claude -ArgumentList \"-p\"'",
    "bash -c 'pwsh -c \"claude -p x\"'"], c => assert.ok(nestedSessionDenial(c), c));
  each(['claude.exe --version', 'CLAUDE --help', "pwsh -c 'Get-ChildItem'", 'cmd /c dir', "cmd //c 'echo hello'",
    'powershell -Command "Write-Output ok"', 'pwsh -File build.ps1'], c => assert.equal(nestedSessionDenial(c), null, c));
});

test('PowerShell tool commands that start a session are refused, however they spell or hide it', () => {
  each(['claude -p hi', 'claude.exe -p hi', 'CLAUDE.EXE --print hi', '& claude -p hi', '& "C:\\Users\\yatin\\.local\\bin\\claude.exe" -p hi',
    ". 'C:\\Users\\yatin\\.local\\bin\\claude.ps1' -p hi", '.\\claude.exe -p', 'C:\\Users\\yatin\\.local\\bin\\claude.exe -p hi',
    'c' + BT + 'laude -p hi', 'cl' + BT + 'aude.exe -p', '& "c' + BT + 'u{6c}aude" -p hi', 'pwsh -c "claude -p x"',
    'pwsh -NoProfile -Command "& {claude -p x}"', 'powershell -Command claude -p x', 'powershell.exe -EncodedCommand ' + encoded('claude -p x'),
    'pwsh -ec ' + encoded('alnair -p'), 'cmd /c claude -p', 'cmd.exe /c "claude -p x"', 'cmd /c cl^aude -p', 'cmd //c claude -p',
    'cmd /c "@claude -p"', "Start-Process claude -ArgumentList '-p'", "saps claude -Args '-p','x'",
    "Start-Process -FilePath claude.exe -ArgumentList '-p x' -Wait", "start alnair -ArgumentList '--model','gpt'",
    '& (gcm claude) -p hi', '& (Get-Command claude.exe).Source -p hi', 'gc prompt.txt | iex', 'Get-Content x.ps1 -Raw | Invoke-Expression',
    'iex "claude -p x"', "Invoke-Expression -Command 'claude -p x'", 'iex (gc x.ps1 -Raw)', "$c = 'claude'; & $c -p hi",
    '$c = Get-Command claude; & $c -p hi', '$out = claude -p hi', '$out=claude -p hi', 'Get-ChildItem; claude -p hi',
    'Write-Output ok && claude -p hi', 'npm test || claude -p fix', 'echo $(claude -p hi)', 'Write-Output "$(claude -p hi)"',
    'Invoke-Command -ScriptBlock { claude -p hi }', 'Start-Job { alnair -p hi }', 'Get-Item x | ForEach-Object { claude -p $_ }',
    'wsl claude -p hi', 'bash -c "claude -p x"', 'codex exec hi', 'codex.exe e hi',
    "python -c \"import subprocess; subprocess.run(['claude','-p','x'])\"", "claude -p 'open"], c => assert.ok(powershellDenial(c), c));
  each(['claude --version', 'claude.exe --help', '& claude -v', 'claude plugin validate .', 'Select-String claude notes.md',
    'Write-Output "claude -p"', 'Get-Command claude', 'gcm claude.exe', 'git log --grep claude',
    "Get-ChildItem | Where-Object { $_.Name -match 'claude' }", '"claude -p" | Out-Null', 'npm test', 'python -m pytest -q',
    'Get-Content notes.md | Select-String alnair', "$name = 'claude'; Write-Output $name", 'cmd /c dir', 'pwsh -NoProfile -Command Get-Date',
    'Start-Process notepad.exe -ArgumentList notes.txt', '# claude -p in a comment', '<# claude -p #> Get-Date'],
  c => assert.equal(powershellDenial(c), null, c));
});

test('Bash commands that write the settings files through a Windows path, PowerShell or cmd are refused; reads are not', () => {
  for (const home of WIN_HOMES) {
    each(["echo x > 'C:\\Users\\yatin\\.claude\\settings.json'", 'echo x > C:/Users/yatin/.claude.json', 'echo x > /c/Users/yatin/.claude.json',
      'rm -rf /c/Users/yatin/.claude', "cp x.json 'C:\\Users\\yatin\\.claude\\settings.json'", 'mv /c/Users/yatin/.claude.json /tmp/x',
      "pwsh -c 'Set-Content ~/.claude.json x'", "pwsh -NoProfile -Command 'Remove-Item -Recurse -Force $HOME\\.claude'",
      'cmd /c "del %USERPROFILE%\\.claude.json"', 'cmd //c "echo {} > %USERPROFILE%\\.claude\\settings.json"',
      'powershell -EncodedCommand ' + encoded('Set-Content $env:USERPROFILE\\.claude.json x'), 'pwsh -e not*base64',
      'robocopy . /c/Users/yatin/.claude settings.json', 'xcopy x.json C:/Users/yatin/.claude.json'],
    c => assert.ok(settingsWriteDenial(c, home), home + ' ' + c));
    each(["cat 'C:\\Users\\yatin\\.claude\\settings.json'", "pwsh -c 'Get-Content ~/.claude/settings.json'",
      'cmd /c "type %USERPROFILE%\\.claude.json"', "cp 'C:\\Users\\yatin\\.claude.json' /tmp/backup.json"],
    c => assert.equal(settingsWriteDenial(c, home), null, home + ' ' + c));
  }
  // The same lines from a Mac: the PowerShell and cmd text is still read.
  assert.ok(settingsWriteDenial("pwsh -c 'Remove-Item -Recurse ~/.claude'", '/Users/u'));
  assert.ok(settingsWriteDenial('echo {} > ~/.claude.json', '/Users/u'));
});

test('PowerShell tool commands that write the settings files are refused; reads and other files are not', () => {
  for (const home of WIN_HOMES) {
    each(["Set-Content -Path $env:USERPROFILE\\.claude\\settings.json -Value '{}'", "'{}' | Out-File ~\\.claude.json",
      'Add-Content "$HOME\\.claude.json" x', 'Remove-Item -Recurse -Force ~\\.claude', 'ri C:\\Users\\yatin\\.claude\\*',
      'del C:/Users/yatin/.claude.json', 'Clear-Content ${env:USERPROFILE}\\.claude.json',
      'Copy-Item x.json -Destination "${env:USERPROFILE}\\.claude\\settings.json"', 'Copy-Item -Destination ~\\.claude\\settings.json -Path x.json',
      'Move-Item ~\\.claude.json C:\\tmp\\x.json', 'Rename-Item ~\\.claude\\settings.json old.json', "'{}' > $env:USERPROFILE\\.claude.json",
      "'x' *> C:/Users/yatin/.claude.json", "'x' >> ~\\.claude\\settings.json", 'Get-Date 2> ~\\.claude.json',
      "[IO.File]::WriteAllText(\"$env:USERPROFILE\\.claude.json\", '{}')", "[System.IO.File]::Delete('C:\\Users\\yatin\\.claude\\settings.json')",
      "$p = \"$HOME\\.claude.json\"; Set-Content $p '{}'", "$p = Join-Path $HOME '.claude.json'; Set-Content -Path $p -Value '{}'",
      "Set-Content (Join-Path $HOME '.claude.json') '{}'", "Set-Location ~\\.claude; Set-Content settings.json '{}'",
      'cd C:\\Users\\yatin\\.claude; ni -Force settings.json', 'Set-Content -Path:~\\.claude.json -Value x', 'SET-CONTENT C:\\USERS\\YATIN\\.CLAUDE.JSON x',
      'New-Item -ItemType SymbolicLink -Path ~\\.claude.json -Target x', 'cmd /c "copy x.json %USERPROFILE%\\.claude.json"',
      'cmd /c del /q %USERPROFILE%\\.claude.json', "pwsh -c 'Remove-Item ~/.claude.json'",
      "Start-Process cmd -ArgumentList '/c','del %USERPROFILE%\\.claude.json'", "iex 'Set-Content ~\\.claude.json x'",
      'robocopy . $HOME\\.claude settings.json', 'robocopy C:\\empty $HOME\\.claude /MIR', 'Remove-Item -Recurse $HOME',
      "Set-Content ~\\.claude.json 'x"],
    c => assert.ok(powershellSettingsDenial(c, home), home + ' ' + c));
    each(['Get-Content ~\\.claude\\settings.json | ConvertFrom-Json', 'Test-Path $env:USERPROFILE\\.claude.json', 'gc C:\\Users\\yatin\\.claude.json -Raw',
      'Copy-Item -Path ~\\.claude\\settings.json -Destination C:\\tmp\\backup.json', 'Copy-Item ~\\.claude.json C:\\tmp\\backup.json',
      'Select-String -Path ~\\.claude\\settings.json -Pattern model', "Set-Content notes.txt 'see ~/.claude.json'", 'Get-ChildItem $HOME\\.claude',
      "[IO.File]::ReadAllText(\"$env:USERPROFILE\\.claude.json\")", 'Set-Content C:\\Users\\yatin\\.claude\\notes.md x'],
    c => assert.equal(powershellSettingsDenial(c, home), null, home + ' ' + c));
  }
});

test('a script that hands the PowerShell tool a session or a settings write is refused before it runs', () => {
  assert.ok(scriptDenial("await tools.PowerShell({command: 'claude -p hi'})", HOME));
  assert.ok(scriptDenial('await PowerShell({command: "Set-Content $env:USERPROFILE\\\\.claude.json x"})', HOME));
  assert.ok(scriptDenial("await tools.Write({file_path: process.env.USERPROFILE + '/.claude.json', content: '{}'})", HOME));
  assert.equal(scriptDenial("await tools.PowerShell({command: 'Get-Content ~/.claude.json'})", HOME), null);
});

test('the PowerShell reader takes quotes, escapes, here-strings, subexpressions and redirections as PowerShell does', () => {
  const r = splitPowerShell("& 'C:\\a b\\x.exe' -p \"it''s\" 'it''s' a" + BT + "b $(Get-Date) > out.txt 2>&1; Write-Output @'\nclaude -p\n'@");
  assert.deepEqual(r.statements[0].words, ['C:\\a b\\x.exe', '-p', "it''s", "it's", 'ab', '\0']);
  assert.equal(r.statements[0].call, true);
  assert.deepEqual(r.subs, ['Get-Date']);
  assert.deepEqual(r.writes, ['out.txt']);
  assert.deepEqual(r.statements[1].words, ['Write-Output', 'claude -p']);
  assert.equal(splitPowerShell("Write-Output 'open"), null);
  assert.equal(splitPowerShell('Get-Date }'), null);
  assert.deepEqual(hostCommand('pwsh', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', 'claude', '-p']), {text: 'claude -p'});
  assert.deepEqual(hostCommand('cmd', ['/d', '/s', '/c', 'claude -p']), {text: 'claude -p', cmd: true});
  assert.deepEqual(hostCommand('pwsh', ['-NoLogo']), {});
  assert.deepEqual(startProcessWords(['-FilePath', 'claude', '-ArgumentList', '-p', ',', 'x', '-Wait']), ['claude', '-p', 'x']);
  assert.equal(windowsPath('C:\\Users\\yatin\\x', null), '/c/Users/yatin/x');
  assert.equal(windowsPath('%USERPROFILE%\\x', '/c/Users/yatin'), '/c/Users/yatin/x');
  assert.equal(windowsPath('\\\\?\\D:\\x', null), '/d/x');
});
