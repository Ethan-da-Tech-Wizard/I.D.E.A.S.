<SYSTEM_CONTEXT>
The application is "I.D.E.A.S." - a God-Tier, 5-in-1 creative software suite designed to be the ultimate and only application a creator needs. It combines a coding environment, music production, game development, artificial intelligence, and visual post-production into a single native ecosystem.
</SYSTEM_CONTEXT>

<ACRONYM_DEFINITION>
I = IDE (Integrated Development Environment)
D = DAW (Digital Audio Workstation)
E = Engines (Game Engines & 3D Environments)
A = AI (Artificial Intelligence Agents)
S = Studio (Video Editing, Photo Editing, Emulators)
</ACRONYM_DEFINITION>

<CORE_DIRECTIVES>
1. SECURITY-FIRST COHESION: The application must integrate these 5 pillars through explicit, validated capabilities. Code written in the IDE may interact with the DAW only through approved commands such as literal `play_sample("relative.wav")` calls or future reviewed capability APIs.
2. PRESERVATION OF EXISTING PILLARS: When building new features, NO AI AGENT shall deprecate, break, or remove existing safe engines. The DAW must retain isolated timing from the main UI thread. The IDE must retain safe editing and validated audio-command execution, not arbitrary host execution.
3. HIGH-PERFORMANCE NATIVE EXECUTION: The suite must remain highly performant while minimizing attack surface. Avoid web-wrappers, embedded browsers, telemetry, network clients, and unreviewed native decoders. Use native Qt and low-level bindings only when the threat model and tests are updated first.
4. FAIL-CLOSED ARCHITECTURE: If a feature requires plugins, terminal access, Git/network operations, AI agents, emulators, arbitrary code execution, or media decoders, it must be disabled until the threat model, privacy policy, audit rules, tests, sandboxing, and release process explicitly cover it.
</CORE_DIRECTIVES>

<ARCHITECTURE_STATE>

  <PILLAR_1_IDE>
  STATUS: IMPLEMENTED
  DESCRIPTION: A secure code editor for writing and reviewing code, plus a constrained audio-command runner for DAW interaction.
  CONSTRAINTS:
  - Arbitrary Python, shell, subprocess, terminal, and host-command execution are forbidden in secure mode.
  - The current safe runner uses AST validation and permits only reviewed audio commands.
  - Future code execution requires a separate reviewed OS sandbox, resource limits, no network by default, private filesystem mounts, explicit user consent, and new tests.
  </PILLAR_1_IDE>

  <PILLAR_2_DAW>
  STATUS: IMPLEMENTED
  DESCRIPTION: A professional-grade music production environment.
  FEATURES:
  - High-priority threaded audio engine (AudioEngineThread) for sample-accurate, zero-jitter playback.
  - Dual-dropdown K.I.S.S. sample navigation.
  - 16-step grid sequencers, bounded offline WAV export, and a built-in offline FX rack (Distortion, Delay, Reverb).
  CONSTRAINTS:
  - Audio timing MUST remain isolated from the main UI thread.
  - Export must remain bounded, local-only, and limited to validated user-selected `.wav` outputs.
  - Sample paths must remain relative bundled WAV names.
  </PILLAR_2_DAW>

  <PILLAR_3_ENGINES>
  STATUS: PLANNED
  DESCRIPTION: A built-in sandbox for creating interactive worlds. Includes a 2D/3D Game Engine and a node-based 3D environment/shader builder.
  FEATURES: Will include a full built-in physics simulator for handling gravity, collisions, and ragdolls natively.
  </PILLAR_3_ENGINES>

  <PILLAR_4_AI>
  STATUS: PLANNED
  DESCRIPTION: Integrated AI collaborative agents that act as peer developers.
  AUTONOMY: The AI has explicit permission to write, modify, and execute code directly in the IDE on its own.
  PERSONA: The AI operates as a silent, invisible workhorse—executing tasks perfectly without unnecessary conversational overhead.
  </PILLAR_4_AI>

  <PILLAR_5_STUDIO>
  STATUS: PLANNED
  DESCRIPTION: The visual and post-production powerhouse. Includes:
  - Video Editor: Built primarily for heavy Visual FX (VFX) work, while maintaining robust timeline splicing/cutting for standard video editing.
  - Photo/Image Editor: Advanced graphic design and manipulation tools. Fully supports BOTH pixel/raster graphics (like Photoshop) and scalable vector graphics (like Illustrator).
  - Emulators: Built-in virtual machine/retro console emulators moddable via the IDE.
  </PILLAR_5_STUDIO>

</ARCHITECTURE_STATE>

<USER_INTERFACE_PARADIGM>
The application will utilize a fully modular "Dockable Panel" system (similar to Premiere Pro or Visual Studio). This allows creators to drag, drop, and snap tools side-by-side, enabling true multi-tasking (e.g., having the DAW open on the left half of the screen while writing game engine code on the right half).
</USER_INTERFACE_PARADIGM>

<NETWORK_&_COLLABORATION>
I.D.E.A.S. is not just single-player. It will feature real-time multiplayer collaboration (similar to Google Docs or Figma), allowing multiple creators to connect and build code, music, and video games together simultaneously. 
ARCHITECTURE: It will utilize a Peer-to-Peer (P2P) network architecture for enhanced privacy and direct connectivity.
DISTRIBUTION: Rather than a monetized store, the platform will heavily support Open-Source Distribution, allowing creators to seamlessly share assets, scripts, and presets for free.
</NETWORK_&_COLLABORATION>

<EXTENSIBILITY_&_PERFORMANCE>
- IDE COMPILATION: While currently running Python, the IDE will add native support for compiling C++ and Rust to handle extreme performance tasks (e.g., custom shaders, physics calculations).
- DAW PLUGINS: The DAW will initially launch as a closed ecosystem, but the core audio engine MUST be architected to easily support third-party VST3/AU plugins in the future.
- MEDIA EXPORTING: The Video Editor will support both exporting standard `.mp4` files locally AND publishing directly to social media APIs (YouTube, TikTok, etc.).
- HARDWARE ACCELERATION: The Engines (Game/VFX) must automatically detect hardware and leverage the GPU (Vulkan/Metal/CUDA) for extreme speed when available, but MUST maintain robust CPU fallbacks for lower-end machines.
</EXTENSIBILITY_&_PERFORMANCE>

<ECOSYSTEM_TOOLS>
- ASSET MANAGER: The suite will feature a unified, beastly "Asset Manager" to organize all 3D models, audio samples, and code snippets in one window.
- VERSION CONTROL: Native Git integration will be built directly into the IDE, allowing users to commit and push without leaving the application.
- CROSS-PLATFORM: The architecture must be designed with eventual touch-screen support in mind so that I.D.E.A.S. can run natively on Tablets (e.g., iPads).
</ECOSYSTEM_TOOLS>

<AI_BEHAVIOR>
The AI is a highly efficient tool. It can converse and answer questions, but it is primarily a workhorse, not just a chatbot. It is explicitly permitted to optimize code and video renders, but ONLY when proactively called upon by the user. It must never alter code or initiate heavy renders in the background unprompted.
</AI_BEHAVIOR>

<DATA_SERIALIZATION>
CURRENT_STATE: The application serializes all active workspace data into a single `.ideas` JSON file.
FUTURE_STATE: The single-file approach will be entirely replaced by a highly organized Workspace Folder architecture. Related assets (code, DAW audio samples, 3D game models, VFX video clips) will be saved in logical, separate sub-directories. Unrelated assets will NEVER be crammed into the same space. Folder projects must validate every path, reject traversal, cap sizes, and keep generated/cache data under app-owned storage.
</DATA_SERIALIZATION>

<UI_&_CUSTOMIZATION>
- THEMES: The application will support custom, sharable user-created themes, allowing creators to build and distribute their own UI color palettes.
</UI_&_CUSTOMIZATION>

<HARDWARE_INTEGRATION>
- MIDI CONTROLLERS: The DAW must natively support all external MIDI keyboards and hardware controllers (MPCs, Launchpads, etc.).
</HARDWARE_INTEGRATION>

<EMULATOR_AI>
- MEMORY HACKING: Retro emulators will support custom save states and direct memory manipulation from the IDE. This is specifically designed to allow users to build AI bots that can play retro games natively inside the app.
</EMULATOR_AI>

<OFFLINE_FIRST>
The application must be architected to be as close to 100% functional as possible while completely offline. While features like real-time multiplayer will naturally require internet, core creation and local AI models must work flawlessly without a connection.
</OFFLINE_FIRST>

<LICENSING>
I.D.E.A.S. is completely free forever. It is licensed under the GNU General Public License v3.0 (GPL-3.0). Any derivative works or distributions of this code MUST remain open-source under the exact same license.
</LICENSING>

<SECURITY_GOVERNANCE>
- The canonical security documents are `SECURITY.md`, `docs/THREAT_MODEL.md`, `docs/PRIVACY.md`, `docs/RELEASE_SECURITY.md`, and `docs/PRODUCTION_SIGNING.md`.
- Before adding a planned pillar feature, update those documents and add negative tests.
- `scripts/security_audit.py`, `scripts/dependency_policy.py`, and the test suite must pass before build or release.
- Telemetry, analytics, embedded web views, local HTTP servers, and automatic updates are forbidden. Remote Git operations and script execution are permitted only via reviewed sandboxed wrappers.
- AI-driven code modifications must pass the validation loop (pytest and security audit verification) in app/ai/core.py and obtain user signature via cache/ai_proposal.json before merging.
</SECURITY_GOVERNANCE>

<STRICT_CONSTRAINTS>
- NEVER overwrite or delete this manifest.
- ALWAYS refer to this document before planning architectural changes.
- ALWAYS prioritize security boundaries before UI aesthetics. The interface should feel cutting-edge, professional, and visually polished without weakening sandboxing, privacy, or local-only guarantees.
- Any AI reading this document MUST adhere to these architectural pillars without exception.
</STRICT_CONSTRAINTS>
