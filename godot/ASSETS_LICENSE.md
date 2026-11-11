# OCTA Godot Assets — License & Provenance

This document details the provenance, creation methods, and licensing terms for all 3D character models, materials, animations, and visual assets within the OCTA Godot project (`godot/`).

---

## 1. Provenance & Attribution

- **Project:** OCTA (Open Computer-Tutor Assistant)
- **Author:** OCTA Project Contributors
- **Asset Type:** Procedural 3D Character Model, Materials, and Animations
- **Implementation File:** `godot/scripts/robot_character.gd`
- **Asset Origin:** 100% first-party code created natively for OCTA.
- **External Dependencies:** None. No third-party proprietary meshes, photogrammetry, external textures, sound effects, or copyrighted rigs are included. All geometry is generated procedurally from Godot 4 built-in primitives (`SphereMesh`, `CapsuleMesh`, `CylinderMesh`, `BoxMesh`) and standard PBR materials (`StandardMaterial3D`). All animation clips (`idle`, `listening`, `thinking`, `talking`, `pointing`, `celebrating`) and state machine transitions (`AnimationTree`, `AnimationNodeStateMachine`) are defined natively in GDScript.

---

## 2. License

All character assets, GDScript source files, shaders, scenes, and project configurations in this directory are released under the **MIT License**, consistent with the root OCTA project license.

```text
MIT License

Copyright (c) 2026 OCTA Contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
