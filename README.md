# rpaste

Content bridge for pasting images into terminal AI agents over SSH.

Copy an image on your local machine, press a tmux keybinding on the remote, and the agent sees it — no X server, no kitty, no special terminal required.

## The problem

When you SSH into a remote machine and run an AI agent (Claude Code, Kiro CLI, etc.), you can't paste images. Terminals only carry text over SSH. Your clipboard is on your local machine; the agent is on the remote.

## How it works

```
You (thalia)                             Agent (minerva)
  clipboard image                          rpaste paste
  ←── ssh rpaste get ──────────────────→   pulls image bytes
                                           stages to ~/.local/state/rpaste/
                                           injects ref into agent pane
```

`rpaste` is one script that does different things based on where it runs:

- **On your local machine** (`rpaste get`): extracts the clipboard image and writes it to stdout
- **On the remote** (`rpaste paste`): SSHes back to your local machine, runs `rpaste get`, stages the image, and feeds it to the agent

## Setup

### Install

Copy `rpaste` to `~/.local/bin/` on **both** your local machine and your remotes:

```bash
cp rpaste ~/.local/bin/rpaste
```

Or symlink it from your dotfiles.

### Dependencies

**Local machine (the one with a display):**

| OS | Tool | Install |
|----|------|---------|
| macOS | `pngpaste` (optional, osascript fallback exists) | `brew install pngpaste` |
| Linux (Wayland) | `wl-paste` | Usually pre-installed with your compositor |
| Linux (X11) | `xclip` | `sudo apt install xclip` |

**Remote machine:** Nothing — just `rpaste` itself, `ssh`, and `tmux`.

### Tmux binding

Add to your `~/.tmux.conf`:

```tmux
bind V run-shell "rpaste paste"
```

Now `prefix + V` pulls your local clipboard image and pastes it into whatever agent is in the active pane.

### Linux xclip shim (optional)

On **Linux** remotes, install the shim so agents' native Ctrl+V works transparently:

```bash
rpaste shim-install
```

This creates a symlink `~/.local/bin/xclip → rpaste`. When Claude Code calls `xclip -t image/png -o`, rpaste serves the staged image. Ensure `~/.local/bin` is before `/usr/bin` in your PATH.

## Requirements

- SSH access **back** from the remote to your local machine (reverse SSH). Tailscale makes this trivial.
- `rpaste` installed on both ends.
- `tmux` on the remote (for injection).

## Commands

```
rpaste get              Extract clipboard image → stdout
rpaste stage            Read image from stdin → staging dir
rpaste pull [host]      Pull clipboard from SSH source → stage locally
rpaste inject [path]    Send image ref into current tmux pane
rpaste paste [host]     Full flow: pull + inject
rpaste serve [args]     xclip shim mode (called via symlink)
rpaste shim-install     Install xclip shim in ~/.local/bin
rpaste status           Show current state
rpaste clean [N]        Remove old staged images (keep N, default 10)
```

## How agents receive the image

| Agent | Remote OS | Method |
|-------|-----------|--------|
| Claude Code | Linux | Ctrl+V via xclip shim (transparent) |
| Claude Code | macOS | Path injection (file path sent to prompt) |
| Kiro CLI | any | `/paste` command |
| Other agents | any | File path sent to prompt |

## License

MIT
