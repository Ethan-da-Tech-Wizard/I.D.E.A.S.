# Engine Pillar Start-to-End Goal

This document records the product direction for the Engine pillar based on
Ethan's answers after the first Agent 1 implementation pass. It is additive:
it does not replace the README, architecture document, threat model, privacy
policy, or feature security review process.

## North Star

The Engine area should become a native, offline Unity/Godot-style workspace
inside I.D.E.A.S. It should have its own dedicated app area because the engine
needs more than one tab: a scene viewport, shader graph, scene tree,
properties/inspector panels, console/log panels, and eventually asset and
simulation tooling. The user should not have to squeeze the engine into the IDE
editor surface.

The first complete product shape is:

- A dedicated Engine mode in the activity bar.
- A central interactive viewport.
- Toggleable dock panels for scene tree, properties, console, physics, assets,
  and shader tools.
- UI-based scene creation and editing.
- Object selection and manipulation.
- Camera/grid tools.
- Serious adjustable physics controls.
- Node-based shaders that emit real shader/code descriptors, while compilation
  remains behind a later reviewed compiler/backend boundary.
- Custom theme import/export so each user can make the app feel like their own
  workspace.
- Per-mode dock layouts so IDE, DAW, Engine, and Studio can each persist a
  layout appropriate to that workflow.

## Workflow Contract

The intended Engine workflow is:

1. Open the Engine mode from the activity bar.
2. Use the toolbar or scene panel to create scene objects.
3. Select objects in the viewport or scene tree.
4. Move objects directly in the viewport or edit numeric values in properties.
5. Toggle grid/snap/camera framing tools while building the scene.
6. Configure physics parameters such as gravity, bounds, mass, velocity, and
   collider size.
7. Press Play/Pause/Reset to simulate physics without changing the saved edit
   baseline unless the user explicitly keeps the result.
8. Build shader graphs visually.
9. Export shader graph descriptors/code text for later reviewed compilation or
   runtime interpretation.
10. Customize the app with imported/exported QSS themes.
11. Save workspace state and restore viewport, shader graph, selected layout,
   and mode-specific panels.

## Product Priorities

The next development priorities are ordered by product impact:

1. Scene editing UI: create/delete/select primitives without writing code.
2. Viewport manipulation: selection outline, drag-to-move, grid display, snap,
   zoom-to-fit and reset camera.
3. Physics playback controls: play, pause, reset, gravity/bounds adjustment,
   and a visible simulation state.
4. Properties panel integration: selected object metadata and editable fields.
5. Shader descriptors: deterministic code/descriptor generation from shader
   graph JSON.
6. Theme import/export: local-only validated QSS copying and preference
   persistence.
7. Per-mode layouts: each major mode owns its own dock-layout preference.

## Security Architecture

The Engine pillar must remain local-first and native. It may render QPainter
graphics, maintain pure Python scene/physics data, and serialize JSON state.
It must not add web frameworks, embedded browsers, HTTP servers, telemetry,
automatic downloads, shell execution, dynamic Python execution, or plugin
loading.

Shader generation is descriptor/code-text generation only. It must not compile,
load, execute, or inject shader code into a GPU runtime until a separate threat
model, audit rule set, and compiler/runtime boundary are approved.

Theme import/export must validate paths, cap QSS size, accept only `.qss`, copy
local files only, and reject traversal-shaped theme names. Imported themes are
appearance data, not executable extensions.

Per-mode layout persistence must store opaque Qt layout bytes only under
validated app-owned configuration paths or workspace-owned cache paths.

## First Deepening Slice

The first deepening slice should implement:

- Viewport selection and drag movement.
- Grid visibility, grid size, snap-to-grid, reset camera, and zoom-to-fit.
- Engine workspace toolbar for object creation and physics playback.
- Scene object creation directly from UI buttons.
- Physics settings and simulation loop in the Engine workspace.
- Shader graph descriptor generation as deterministic text.
- Theme import/export service methods.
- Panel layout persistence helpers keyed by mode name.

This slice intentionally avoids:

- Real GPU shader compilation.
- Complex asset imports.
- Native plugin/runtime loading.
- Networked collaboration.
- Undo/redo for every scene edit.
- Saving binary rendered previews.

Those can be layered later after the security model and tests are expanded.

## Refinement Pass 2 Direction

The next direction is to keep pushing toward a serious next-generation
developer tool rather than a minimal demo. The Engine should favor depth and
capability when that can be done without weakening local-first security. The
next product slice therefore prioritizes the full editing loop:

- Parent/child hierarchy should affect transforms now. Moving a parent should
  move its children in the viewport so hierarchy is not merely decorative.
- Inspector editing should cover the properties creators expect first: id/name,
  position, size, color, shape, physics mass, velocity, collider size,
  static/dynamic, trigger state, material response, and collision layer.
- Manipulation should stay move-first in the viewport for this pass, with
  inspector-driven resize/rotation/material editing. Resize and rotation gizmos
  can follow after undo/redo and inspector state are reliable.
- Undo/redo should start now for creation, deletion, duplication, movement, and
  inspector edits.
- Scenes should continue saving inside workspace state, but standalone
  `.ideas-scene` import/export should exist so users can share, archive, and
  reuse Engine scenes independently.
- Physics should gain object-level material controls: restitution/bounce,
  friction, damping, gravity scale, trigger flag, and collision layer.
- Collision events should remain data-only for now. Trigger state can be stored
  and shown, but runtime scripting/event callbacks need a later reviewed
  capability boundary.
- Shader output should remain an internal I.D.E.A.S. descriptor that can later
  target GLSL, Godot-style shader text, or other backends.
- Theme customization needs UI eventually, but this pass strengthens safe
  backend and Engine workspace hooks first.
- Panel UX should eventually include View menu, toolbar dropdown, and
  shortcuts; this pass can add manager methods and persistable toggles as
  groundwork.
- Asset references should wait for the Asset Manager contract to stabilize.
- Keyboard shortcuts should start with delete selected, duplicate, undo/redo,
  play/pause, frame selected, grid, and snap.
- Selection can remain single-selection in this pass; multi-select group
  editing should follow once undo/redo and inspector edits are stable.
- Viewport bookmarks are useful, but one editor camera plus frame/fit tools are
  enough for this pass.

The finish bar for this pass is: select, inspect, edit, undo, redo, duplicate,
delete, save/export/import scene, and run richer physics metadata safely.

## Refinement Pass 3 Direction

The docked Scene Tree and Properties panels should stop being placeholders.
They are part of the Unity/Godot-style workflow and need to reflect live Engine
state:

- Scene Tree should display the actual scene graph hierarchy, including nested
  children.
- Selecting an item in Scene Tree should select the same object in the viewport.
- When viewport selection changes, Scene Tree should follow that selection.
- Properties should mirror the selected node's inspector fields.
- Applying a Properties panel edit should use the same validated Engine
  workspace update path as the Inspector tab.
- These panels should remain normal toggleable dock panels managed by
  `PanelManager`, so Engine can keep its own workspace layout.

This pass intentionally keeps the dock panels in-process and data-bound only.
They do not browse files, execute scripts, decode assets, or add runtime event
callbacks.
