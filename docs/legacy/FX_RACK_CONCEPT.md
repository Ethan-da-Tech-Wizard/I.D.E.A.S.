# NeonDAW FX Rack

`FXRack` is a standalone React component that owns a Tone.js insert chain:

```text
Tone.Channel input -> Tone.Distortion -> Tone.PingPongDelay -> Tone.Reverb -> Tone.Channel output
```

Mount it from the main app when the integration branch is ready:

```tsx
import { useRef } from 'react';
import FXRack, { type FXRackHandle } from './components/FXRack';

function App() {
  const fxRackRef = useRef<FXRackHandle>(null);

  return <FXRack ref={fxRackRef} />;
}
```

Pipe any Tone.js source into the rack by connecting to the exposed `input` channel:

```ts
const synth = new Tone.PolySynth(Tone.Synth);
synth.connect(fxRackRef.current!.input);
```

By default, the rack connects its master output to `Tone.Destination`. If the host app needs custom routing, disable that and connect the `output` channel manually:

```tsx
<FXRack
  ref={fxRackRef}
  connectToDestination={false}
  onReady={(rack) => {
    rack.output.connect(customBus);
  }}
/>
```

The component is intentionally isolated under `src/components/FXRack/` and uses a CSS module, so it does not require changes to `src/App.tsx` or `src/index.css`.

## UI Themes

The rack includes a built-in show/hide UI toggle. The Tone.js nodes stay mounted and connected while the controls are collapsed.

There are 10 crisp visual presets:

```text
Tokyo, Arctic, Chrome, Ember, Mint, Violet, Laser, Graphite, Signal, Ruby
```

The 11th option is `Custom`, which exposes color controls for primary accent, secondary accent, background, and panel color.
