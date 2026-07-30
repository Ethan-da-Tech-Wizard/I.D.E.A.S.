# Visual Step Sequencer

Standalone, visual-only step sequencer for NeonDAW. It owns no audio logic —
it renders a pattern grid and a playhead, and reports clicks back to the
parent, which is responsible for driving Tone.js.

## Component: `StepSequencer`

Controlled component — the parent owns the pattern data.

### Props

| Prop | Type | Required | Description |
|---|---|---|---|
| `steps` | `boolean[][]` | yes | `steps[trackIndex][stepIndex]`. Any track count x step count works (built for 16 steps / 4-8 tracks). |
| `currentStep` | `number` | yes | Column to highlight as the playhead. Pass `-1` while stopped. |
| `onStepToggle` | `(trackIndex: number, stepIndex: number) => void` | yes | Fired when the user clicks a cell. Flip that cell in your state. |
| `trackLabels` | `string[]` | no | Row labels. Defaults to `['Kick', 'Snare', 'Clap', 'Hi-Hat', 'Tom', 'Perc']`. |
| `className` | `string` | no | Extra class on the outer wrapper. |

### Helper

`createEmptyGrid(numTracks = 6, numSteps = 16): boolean[][]` — convenience
initializer for `steps` state.

## Mounting

```tsx
import { useState } from 'react';
import { StepSequencer, createEmptyGrid } from './components/Sequencer';

function Example() {
  const [steps, setSteps] = useState(() => createEmptyGrid(6, 16));
  const [currentStep, setCurrentStep] = useState(-1); // drive from Tone.Transport

  const handleStepToggle = (track: number, step: number) => {
    setSteps(prev =>
      prev.map((row, t) => (t === track ? row.map((v, s) => (s === step ? !v : v)) : row))
    );
  };

  return (
    <StepSequencer
      steps={steps}
      currentStep={currentStep}
      onStepToggle={handleStepToggle}
    />
  );
}
```

### Driving the playhead from Tone.js

This component never imports Tone. To animate the playhead, schedule a
16th-note repeat on `Tone.Transport` in the audio engine and push the index
into whatever state feeds `currentStep`:

```ts
Tone.Transport.scheduleRepeat((time) => {
  const step = index % steps[0].length;
  Tone.Draw.schedule(() => setCurrentStep(step), time);
  index++;
}, '16n');
```

Reading `steps` inside that callback is how the engine decides which
tracks/instruments to trigger on a given step.

## Styling

No new theme was introduced. The component reuses NeonDAW's existing tokens
from `src/index.css` (`--accent-primary`, `--accent-secondary`,
`--panel-border`, `--text-primary`, `--text-secondary`, `--font-family`) and
the global `.glass-panel` class, plus a scoped `StepSequencer.module.css` for
grid/glow layout so it can't leak styles into or collide with the rest of the
app.

`App.tsx` and `index.css` were intentionally left untouched to avoid
conflicts with other in-flight work on the code-to-music engine.
