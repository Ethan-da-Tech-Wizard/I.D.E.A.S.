# Salvaged from earlier prototypes

This project went through two earlier, now-deleted implementations before
landing on the PySide6/Qt Widgets architecture in `ARCHITECTURE.md`:

1. A React + Vite + Tauri web-shell app (`src/`, `src-tauri/`, `package.json`, ...)
2. A C#/.NET WPF app (`I.D.Wpf/`)

Both were deleted because they used technology explicitly prohibited by
`ARCHITECTURE.md`. Nothing from either was directly code-reusable (different
languages/frameworks), but the following was carried forward:

- **`app/resources/samples/`** — all 83 original `.wav` files (electronic,
  instruments, sfx) from the old `public/samples/`, plus 60 procedurally
  generated wubs/bass/leads/pads/plucks/sfx added via
  `scripts/generate_synth_samples.py` (143 total). Referenced by
  `MainWindow.play_sample_by_name()` in `app/application.py` and by
  `DawWorkspace`/`DawTrack` in `app/ui/daw.py`.
- **`sample-manifest.legacy.json`** — superseded by
  `security.list_bundled_samples()`, which walks the real directory live.
  Kept only as a historical reference, not used by the app.
- **`FX_RACK_CONCEPT.md`** — **implemented.** The Distortion -> Delay ->
  Reverb chain lives in `app/services/audio_fx.py` (pure-stdlib, rendered
  offline to a cached WAV per sample since QSoundEffect/QMediaPlayer have no
  live DSP graph), with the toggle UI in `app/widgets/fx_rack.py`. The 10
  named color presets became `THEME_PRESETS` in the same widget file, applied
  via `DawWorkspace.apply_theme()`; "Custom" opens a `QColorDialog` instead of
  the original's four separate pickers.
- **`STEP_SEQUENCER_CONCEPT.md`** — **implemented**, independently, as the
  16-step grid already built into `DawTrack` in `app/ui/daw.py` (`steps`
  list, `current_step` playhead, per-track step buttons). Same data model
  described in the doc, just native Qt instead of a controlled React
  component.

Not salvaged (pure web/npm/Tauri/WPF scaffolding, no reusable logic):
`node_modules/`, `dist/`, `src-tauri/`, `src/App.tsx`, `src/components/CodeEditor.tsx`,
`src/components/SampleList.tsx`, build configs (`vite.config.ts`, `tsconfig*.json`,
`package.json`), `generate-manifest.js`, `I.D.Wpf/` (all `.xaml`/`.cs`/`.csproj`).
