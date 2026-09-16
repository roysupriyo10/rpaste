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
                                           agent reads via shim / dylib
```

`rpaste` is one script that does different things depending on where it runs:

- **On your local machine** (`rpaste get`): extracts the clipboard image, writes to stdout
- **On the remote** (`rpaste paste`): SSHes back to your machine, runs `rpaste get`, stages the image

How agents receive the image depends on the remote OS:

| Remote OS | Mechanism | Why |
|-----------|-----------|-----|
| Linux | xclip shim | `Bun.Image.fromClipboard()` returns null on Linux, so Claude Code falls back to calling `xclip` from PATH. A symlink named `xclip` pointing to rpaste intercepts the call and serves the staged image. |
| macOS | DYLD injection | Claude Code calls `NSPasteboard` directly (in-process, native Obj-C). The macOS pasteboard is inaccessible from SSH sessions (per-session bootstrap namespace). A small dylib loaded via `DYLD_INSERT_LIBRARIES` swizzles `NSPasteboard` to fall back to the staged image when the real pasteboard is empty. |

## Install

Get rpaste on **both** your local machine and your remotes:

```bash
# clone and link
git clone https://github.com/roysupriyo10/rpaste.git
ln -s $(pwd)/rpaste/rpaste ~/.local/bin/rpaste
```

Then run the one-time setup:

```bash
rpaste install
```

This will:
- Link rpaste into your PATH
- **Linux**: install the xclip shim symlink
- **macOS**: build the NSPasteboard dylib and add a shell profile snippet that loads it in SSH sessions
- Verify reverse SSH access back to your local machine

No wrappers, no aliases — you launch `claude` normally.

### Dependencies

**Local machine (the one with a display):**

| OS | Tool | Install |
|----|------|---------|
| macOS | `pngpaste` (optional, swift fallback exists) | `brew install pngpaste` |
| Linux (Wayland) | `wl-paste` | Usually pre-installed |
| Linux (X11) | `xclip` | `sudo apt install xclip` |

**macOS remote:** Xcode Command Line Tools (for building the dylib).

**Linux remote:** Nothing besides `rpaste` itself, `ssh`, and `bash`.

### Requirements

- SSH access **back** from the remote to your local machine (reverse SSH). Tailscale makes this trivial.
- `rpaste` installed on both ends.

## Usage

```bash
# 1. Copy/screenshot an image on your local machine
# 2. On the remote:
rpaste paste

# 3. Ctrl+V in the agent — the image is available
```

## Commands

```
rpaste install          One-time setup
rpaste uninstall        Remove shims, DYLD snippet, and PATH link
rpaste get              Extract clipboard image → stdout
rpaste paste [host]     Pull clipboard from SSH source, stage, make available
rpaste status           Show current state
rpaste clean [N]        Remove old staged images (keep N, default 10)
rpaste inject-build     Build the NSPasteboard dylib (macOS, called by install)
rpaste inject-env       Print the shell profile snippet (macOS)
rpaste shim-install     Install xclip shim (Linux, called by install)
```

## Terminal agnostic

rpaste has zero terminal dependencies. It works with Alacritty, foot, Ghostty, iTerm2, kitty, WezTerm, xterm, or any other terminal. The transport is pure SSH; clipboard extraction uses OS-native tools.

## License

MIT
