# rpaste

Paste clipboard images into Claude Code and Codex inside tmux over SSH.

**Path** delivery pastes a quoted image path through tmux's bracketed-paste
support. **Native** delivery publishes the image to the agent's private
clipboard and forwards Ctrl+V. Both share the same transfer and staging code.
Neither submits your prompt.

## Install

Run the dotfiles installer on your desktop and remote machines:

```sh
~/dotfiles/install.sh
```

The core uses SSH, tmux, Python 3.9+ with its standard library, and your
source desktop's clipboard command. It has no pip, npm, or Go dependencies.
There are no architecture-specific binaries to download.

The same installer sets up both delivery methods. Linux native delivery uses
Xvfb and xclip; the installer prefers yay when available. macOS builds a small
AppKit shim using the installed clang toolchain. For a path-only installation,
use `RPASTE_NATIVE=0 ~/dotfiles/install.sh`. Native
clipboard settings apply only to the agent process. Path delivery works
when the optional native tools are absent.

Open a new shell or source your shell configuration after installation.
Start new Claude/Codex sessions so their launchers register the tmux panes.
Existing agent sessions support path delivery; native delivery needs a restart.

## Connect and paste

On the machine with your desktop clipboard:

```sh
ssh user@remote
```

Normal interactive SSH automatically creates a private Unix socket reverse
tunnel to an on-demand clipboard source service. The installer provides the
client wrapper and the remote SSH environment configuration. The remote does
not need separate SSH credentials for your desktop. Noninteractive SSH, remote
commands, scp, and configuration queries use the native client unchanged.
Set `RPASTE_AUTO=0` to disable automatic tunneling for a connection. `rssh` remains
available as an explicit tunnel launcher.

On the remote, attach to tmux and launch `claude` or `codex` normally. Copy an
image locally, then use:

| Key | Delivery |
| --- | --- |
| Ctrl+V | Selected method; path by default |
| Prefix, then v | Path |
| Prefix, then Shift+V | Native |

The default tmux prefix is Ctrl+B. Change the selected method with:

```sh
tmux set -g @rpaste-method native
tmux set -g @rpaste-method path
```

Text clipboard contents are pasted as text. Other panes retain their normal
Ctrl+V behavior. Delivery targets the originating pane and reads the environment
of the client that pressed the key. Reattaching from another machine updates the
source without restarting the agent. Simultaneous clients have separate tunnels;
native clipboards belong to individual panes.

Source sockets are account-private. There is no public TCP listener or fixed
forwarding port. The source service starts when needed and logs to
`~/.local/state/rpaste/source.log`.

## Connections opened before installation

Reconnect after installing on both ends to use the automatic tunnel. The
original reverse SSH transport remains available for older connections:

```sh
export RPASTE_HOST=desktop-user@desktop-host
tmux attach
```

Set the override before attaching so the tmux client carries it. Otherwise,
rpaste uses the triggering client's SSH source address. This fallback needs
working noninteractive SSH access to your desktop. The automatic socket tunnel avoids that
separate authentication and preserves the source's desktop environment.

## Commands

```sh
rpaste status                      # Transport and platform status
rpaste pull [user@host]             # Fetch an image and print its immutable path
rpaste local                       # Stage this machine's clipboard
rpaste run -- codex                 # Explicit launcher
rpaste run -- claude
rpaste clean 10                     # Retain the ten newest staged images
```

Images are private PNG files under `~/.local/state/rpaste/images`. Failed,
corrupt, and interrupted transfers leave the last image intact. Clipboard reads
do not replace the desktop image with a text path.

## Tests

Run from either the dotfiles root or the rpaste repository:

```sh
python3 -m unittest discover -s tests -v
```

Tests cover Unix socket transfers, concurrency, corrupt and interrupted PNGs,
client selection, reverse SSH failures, tmux pane targeting, bracketed paste,
and optional native X11 clipboard isolation. Native tests skip when Xvfb/xclip
are absent. Core tests use temporary files and isolated tmux servers. They do not launch
agents, submit model requests, or alter the desktop clipboard.

For live acceptance, copy a screenshot and try Prefix+v and Prefix+Shift+V in
both agents. Confirm that an image attaches, an existing draft survives, and the
prompt is not submitted. Each CLI update needs this check because the agent UI
is outside rpaste's control. Test macOS native injection on macOS; Linux tests
cannot validate it.

The opt-in acceptance test starts an isolated SSH server, creates real SSH
connections and a real tmux client, checks both delivery methods, and reconnects
from a second source without restarting the pane:

```sh
RPASTE_E2E=1 python3 -m unittest discover -s tests -v
```

It needs Linux, the optional X11 tools, sshd, ssh-keygen, and passwordless sudo
for its temporary SSH server. Its terminal fixture records delivery and reads
the native clipboard; it does not launch a model or claim to test agent UI code.

Installed Claude Code/Codex UI compatibility can also be checked on Linux:

```sh
RPASTE_E2E=1 RPASTE_AGENT_UI=1 python3 -m unittest discover -s tests -v
```

The UI check opens the installed binaries in isolated temporary profiles using
your existing sign-in. It checks native and Unicode path attachments without
submitting a prompt. It requires Xvfb/xclip; macOS needs a separate live check.

The existing `assh`/`fssh` entrypoints delegate to the automatic SSH wrapper.
`kssh` supplies the same tunnel options to Kitty's bootstrap. The system SSH
binary is unchanged, and commands, Git transports, and scp bypass tunnel setup.
