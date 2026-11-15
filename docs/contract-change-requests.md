# Peticiones de cambio de contrato

## 2026-11-14 — shell UI — sin cambio

No se solicita un campo nuevo.

- El aviso de demostración se detecta con `product == "mock"` o `provenance.source` que contenga `mock`. Ambos ya están en el contrato v1.1.
- El slider no necesita `coherence_threshold`. Lee esa clave solo si un artefacto antiguo la trae; en v1.1 el suelo es `thresholds.coherence_min`.
- No reintroducir `coherence_threshold` en `meta.json`.
