# App icon

`icon.html` draws the icon: the spherical audio-wave visualizer (`src/components/SphereWave.svelte`) on the app's dark glass background, in one hue: rings from pale sky blue at the top to a deeper blue at the bottom, on a navy body.

`icon.html` has a few palettes (`bleu`, the default; `ink`, `brass`, and the earlier multi-color `current`). Open `icon.html?p=ink` to see another one, and change the default at `|| PALETTES.bleu`. The body is 824 px on a 1024 px canvas, as in the macOS icon grid.

To change the icon:

1. Edit `icon.html` and open it in a browser to check it.
2. Save the canvas as `icon.png` (1024 × 1024, transparent corners). For example, with Playwright: screenshot the `#c` element with `omitBackground: true`.
3. Regenerate the bundle icons: `pnpm tauri icon icon/icon.png`, then delete the `android/`, `ios/`, `Square*` and `64x64.png` outputs in `src-tauri/icons/` (macOS only).
