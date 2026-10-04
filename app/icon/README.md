# App icon

`icon.html` draws the icon: the spherical audio-wave visualizer (`src/components/SphereWave.svelte`) on the app's dark glass background, using the visualizer's state colors from top to bottom (speaking cyan, idle blue, thinking violet, listening coral). The body is 824 px on a 1024 px canvas, as in the macOS icon grid.

To change the icon:

1. Edit `icon.html` and open it in a browser to check it.
2. Save the canvas as `icon.png` (1024 × 1024, transparent corners). For example, with Playwright: screenshot the `#c` element with `omitBackground: true`.
3. Regenerate the bundle icons: `pnpm tauri icon icon/icon.png`, then delete the `android/`, `ios/` and `Square*` outputs in `src-tauri/icons/` (macOS only).
