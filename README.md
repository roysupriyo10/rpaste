# rpaste

Content bridge for pasting images into terminal AI agents over SSH.

Copy an image on your local machine, run `rpaste paste` on the remote, and the agent sees it — no X server, no special terminal required.

## The problem

When you SSH into a remote machine and run an AI agent (Claude Code, Kiro CLI, etc.), you can't paste images. Terminals only carry text. Your clipboard is on your local machine; the agent is on the remote.

## How it works

```
You (local)                              Agent (remote)
  clipboard image                          rpaste paste
  ←── ssh rpaste get ──────────────────→   pulls image bytes
                                           stages to ~/.local/state/rpaste/
                                           prints path
```

`rpaste` is one script that does different things depending on where it runs:

- **On your local machine** (`rpaste get`): extracts the clipboard image, writes to stdout
- **On the remote** (`rpaste paste`): SSHes back to your machine, runs `rpaste get`, stages the image, prints the path

On Linux remotes, an xclip shim makes agents' native Ctrl+V work transparently.

## Install

Get rpaste on **both** your local machine and your remotes:

```bash
# copy it
cp rpaste ~/.local/bin/rpaste

# or clone and link
git clone https://github.com/roysupriyo10/rpaste.git
ln -s $(pwd)/rpaste/rpaste ~/.local/bin/rpaste
```

Then run the one-time setup:

```bash
rpaste install
```

This will:
- Link rpaste into your PATH
- Install the xclip shim (Linux only — makes Ctrl+V work in agents)
- Check for clipboard tool dependencies
- Verify reverse SSH access back to your local machine

### Dependencies

**Local machine (the one with a display):**

| OS | Tool | Install |
|----|------|---------|
| macOS | `pngpaste` (optional, osascript fallback exists) | `brew install pngpaste` |
| Linux (Wayland) | `wl-paste` | Usually pre-installed |
| Linux (X11) | `xclip` | `sudo apt install xclip` |

**Remote machine:** Nothing besides `rpaste` itself, `ssh`, and `bash`.

### Requirements

- SSH access **back** from the remote to your local machine (reverse SSH). Tailscale makes this trivial.
- `rpaste` installed on both ends.

## Usage

```bash
# 1. Copy/screenshot an image on your local machine
# 2. On the remote:
rpaste paste

# 3. Use the image:
#    - Linux: Ctrl+V in agent (xclip shim serves it)
#    - macOS: paste the printed path into the agent
#    - Any:   reference ~/.local/state/rpaste/images/latest.png
```

## Commands

```
rpaste install          One-time setup: link into PATH, install shims, check deps
rpaste get              Extract clipboard image → stdout
rpaste paste [host]     Pull clipboard from SSH source, stage locally, print path
rpaste status           Show current state
rpaste clean [N]        Remove old staged images (keep N, default 10)
rpaste shim-install     Install xclip shim (called by install on Linux)
```

## How agents receive the image

| Agent | Remote OS | Method |
|-------|-----------|--------|
| Claude Code | Linux | Ctrl+V via xclip shim (transparent) |
| Claude Code | macOS | Paste the printed path into the prompt |
| Kiro CLI | any | `/paste` or paste the path |
| Other agents | any | Paste the path |

## Terminal agnostic

rpaste has zero terminal dependencies. It works with Alacritty, foot, Ghostty, iTerm2, kitty, WezTerm, xterm, or any other terminal. The transport is pure SSH; clipboard extraction uses OS-native tools.

## License

MIT
